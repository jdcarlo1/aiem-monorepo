"""
Staleness filter for AIEM premarket candidates.

Implements the 4-check framework:
  1. Stale-gap detection  (gap already extended 30%+ above open)
  2. Catalyst decay penalty  (-2 if catalyst >24h old; -4 if >48h; -2 if no news)
  3. Move-day calculation    (how many sessions since the gap originated)
  4. VWAP day-2 exhaustion   (price extended above VWAP on fading volume = fade)

Data is passed in directly from the DB — no external API calls needed.

History rows come from _get_multiday_context():
    {scan_date, close_price, open_price, volume, gap_pct, rvol, vwap}
    sorted oldest → newest.

News items come from _aiem_get_news():
    Polygon /v2/reference/news results — each has 'published_utc'.
"""

from datetime import datetime, date, timezone
import logging

logger = logging.getLogger(__name__)

# Minimum final conviction to emit a PASS verdict.
# Mirrors the 9-layer scorer's own threshold so both gates are consistent.
CONVICTION_THRESHOLD = 70


def evaluate_signal_with_data(
    ticker: str,
    base_conviction: float,
    history: list,
    news: list,
    scan_timestamp: datetime,
    premarket_mode: bool = True,
) -> dict:
    """
    Run all 4 staleness checks against a premarket candidate.

    Args:
        ticker           — stock symbol
        base_conviction  — score after _score_multiday() adjustment (0-100)
        history          — list of DB rows (oldest→newest) from _get_multiday_context
        news             — list of Polygon news dicts from _aiem_get_news
        scan_timestamp   — UTC datetime of the current scan
        premarket_mode   — True: skip GAP_IS_YESTERDAY (premarket always sees
                           yesterday's data; that check fires for intraday use)

    Returns dict:
        action           — "PASS" or "SKIP"
        final_conviction — adjusted score
        tags             — list of applied adjustment labels
        reason           — human-readable explanation
    """
    tags: list = []

    # Normalise scan_date
    if hasattr(scan_timestamp, "date"):
        scan_date = scan_timestamp.date()
    else:
        scan_date = scan_timestamp          # already a date

    if not history:
        return {
            "ticker":           ticker,
            "action":           "PASS",
            "final_conviction": round(float(base_conviction), 1),
            "tags":             [],
            "reason":           "No history — pass through unfiltered",
        }

    today_row = history[-1]   # most recent trading day

    # ── Data accessors — read directly from the DB rows ────────────────────

    def _gap_open() -> float:
        return float(today_row.get("open_price") or 0)

    def _cur_price() -> float:
        return float(today_row.get("close_price") or 0)

    def _vwap() -> float:
        return float(today_row.get("vwap") or 0)

    def _gap_date() -> date:
        sd = today_row.get("scan_date")
        if hasattr(sd, "date"):   return sd.date()
        if isinstance(sd, date):  return sd
        return scan_date            # fallback: treat as today

    def _vol_trend() -> str:
        """
        Compares last-two volume bars.
        'decreasing' if today's volume stepped down ≥10% from yesterday's.
        """
        vols = [float(r.get("volume") or 0) for r in history[-2:]]
        if len(vols) == 2 and vols[0] > 0:
            return "decreasing" if vols[1] < vols[0] * 0.90 else "increasing"
        return "increasing"         # not enough data → optimistic

    def _catalyst_ts() -> datetime:
        """Find the most recent Polygon news timestamp for this ticker."""
        for item in (news or []):
            pub = (item.get("published_utc") or
                   item.get("published")     or "")
            if pub:
                try:
                    return datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except Exception:
                    pass
        raise ValueError("no catalyst timestamp found")

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 1: Stale gap
    # ─────────────────────────────────────────────────────────────────────────
    gap_origin = _gap_date()

    # In premarket mode we always look at yesterday's grouped-daily data, so
    # gap_origin == yesterday is expected and NOT a disqualifier on its own.
    # We only hard-skip here during intraday use (premarket_mode=False).
    if not premarket_mode and gap_origin < scan_date:
        return {
            "ticker":           ticker,
            "action":           "SKIP",
            "final_conviction": 0,
            "tags":             ["GAP_IS_YESTERDAY"],
            "reason":           "Gap originated in a prior session",
        }

    # Hard skip if stock already ran >30% above yesterday's open — it's spent.
    gap_open = _gap_open()
    cur      = _cur_price()
    if gap_open > 0 and cur > 0:
        extension = (cur - gap_open) / gap_open
        if extension > 0.30:
            return {
                "ticker":           ticker,
                "action":           "SKIP",
                "final_conviction": 0,
                "tags":             [f"EXTENDED_FROM_GAP({extension:.1%})"],
                "reason":           (f"Already extended {extension:.1%} above "
                                     f"gap open — fade risk"),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 2: Catalyst decay penalty
    # ─────────────────────────────────────────────────────────────────────────
    try:
        cat_ts = _catalyst_ts()
        if cat_ts.tzinfo is None:
            cat_ts = cat_ts.replace(tzinfo=timezone.utc)
        st = (scan_timestamp
              if scan_timestamp.tzinfo
              else scan_timestamp.replace(tzinfo=timezone.utc))
        age_h = (st - cat_ts).total_seconds() / 3600

        if age_h > 48:
            penalty = -4
            tags.append("CATALYST_STALE_48h(-4)")
        elif age_h > 24:
            penalty = -2
            tags.append("CATALYST_AGING_24h(-2)")
        else:
            penalty = 0              # fresh catalyst — no penalty

    except Exception:
        # Nano-caps often gap on low float alone with no formal news.
        # Apply a modest penalty rather than the maximum -4 that a known-stale
        # catalyst would attract.
        penalty = -2
        tags.append("NO_CATALYST(-2)")

    adj = float(base_conviction) + penalty

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 3: Move-day calculation
    # ─────────────────────────────────────────────────────────────────────────
    # In premarket mode gap_origin is yesterday → delta=1 → move_day=2.
    # That's semantically correct: if TNMG gapped +107% yesterday, today is
    # the second day of the move and the exhaustion checks below apply.
    delta    = (scan_date - gap_origin).days
    move_day = max(1, delta + 1)
    if move_day >= 2:
        tags.append(f"MOVE_DAY_{move_day}")

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 4: VWAP day-2 exhaustion
    # ─────────────────────────────────────────────────────────────────────────
    if move_day >= 2:
        vwap = _vwap()
        if vwap > 0 and cur > 0:
            vwap_pos = (cur - vwap) / vwap
            if _vol_trend() == "decreasing" and vwap_pos > 0.05:
                tags.append("DAY2_EXTENDED_ABOVE_VWAP(-3)")
                adj -= 3
                logger.warning(
                    f"[{ticker}] DAY2_EXTENDED_ABOVE_VWAP: "
                    f"{vwap_pos:.1%} above VWAP, volume decreasing"
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Final verdict
    # ─────────────────────────────────────────────────────────────────────────
    adj = round(adj, 1)

    if adj < CONVICTION_THRESHOLD:
        action = "SKIP"
        reason = (f"Conviction {adj} below threshold {CONVICTION_THRESHOLD} "
                  f"after staleness checks")
    else:
        action = "PASS"
        reason = f"Cleared all staleness checks — conviction: {adj}"

    logger.info(f"[{ticker}] {action} | {base_conviction:.0f}→{adj} | {tags}")

    return {
        "ticker":           ticker,
        "action":           action,
        "final_conviction": adj,
        "tags":             tags,
        "reason":           reason,
    }
