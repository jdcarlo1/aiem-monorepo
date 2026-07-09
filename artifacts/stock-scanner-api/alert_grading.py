"""
alert_grading.py — daily forward-return grading for telegram_alert_ledger.

PURPOSE
Phase 3 of the alert-gateway trust pipeline (see alert_gateway.py's module
docstring for the full picture). Once a day this module:

  1. Finds SIGNAL-class, non-test alerts in telegram_alert_ledger that are
     old enough to grade at each horizon (D+1 / D+3 / D+5 calendar days)
     and don't have that outcome column filled in yet.
  2. Looks up the ticker's close price on the nearest available trading
     day at/after the target date — polygon_market_daily first (already
     populated EOD, no live-fetch risk), Tradier quotes as a fallback for
     tickers not yet in polygon_market_daily. Same two-tier lookup pattern
     as aiem_process.py's aiem_grade_t3_t5().
  3. Writes outcome_d1_pct / outcome_d3_pct / outcome_d5_pct as forward
     return from trigger_price (the price captured at send time).
  4. The FIRST time outcome_d3_pct becomes available for a row, that row
     is marked graded=TRUE with win_loss set (D+3 is the decision
     horizon — long enough to filter same-day noise, short enough that
     n>=20 accumulates in weeks not months) and
     meta_learning_signal_trust.update_trust_weight() is called once for
     (signal_source, 'TELEGRAM_ALERTS', win). D1/D5 columns keep filling
     independently and are informational only — they do NOT re-trigger a
     trust update.

SAFETY CONTRACT (matches alert_gateway.py)
- This module only READS alert history and WRITES outcome/trust columns.
  It never touches alert_class, never suppresses a send, and has no path
  back into any live sender. Hard-gating is a separate, explicitly-
  approved future phase.
- Every DB call is wrapped; a single bad ticker or a DB hiccup logs a
  warning and moves on, it never aborts the whole grading pass.
- context_bucket is hardcoded to 'TELEGRAM_ALERTS' so trust weights
  computed here can never mix with PAPER_TRADING / AIEM_MICROCAP /
  AIEM_PREMARKET context buckets used elsewhere in the app.

REQUIRES: DATABASE_URL. Optional: TRADIER_API_TOKEN_2 / TRADIER_API_TOKEN
for the fallback quote lookup (grading still works without it — rows
simply stay ungraded until polygon_market_daily catches up).
"""

import os
import json
import urllib.request
from datetime import timedelta

import psycopg2

import meta_learning_signal_trust as mlst

_DB_URL = os.environ.get("DATABASE_URL")
_TRADIER_TOKEN = (os.environ.get("TRADIER_API_TOKEN_2")
                  or os.environ.get("TRADIER_API_TOKEN", ""))
_CONTEXT_BUCKET = "TELEGRAM_ALERTS"

_HORIZONS = [
    (1, "outcome_d1_pct"),
    (3, "outcome_d3_pct"),
    (5, "outcome_d5_pct"),
]
_DECISION_HORIZON_COL = "outcome_d3_pct"


def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=6)


def _td_quotes(symbols):
    """Minimal standalone Tradier quote fetch (mirrors main.py / aiem_process.py
    _td_quotes — no shared client module exists in this codebase yet)."""
    if not _TRADIER_TOKEN or not symbols:
        return {}
    try:
        batch = ",".join(symbols[:200])
        req = urllib.request.Request(
            f"https://api.tradier.com/v1/markets/quotes?symbols={batch}",
            headers={"Authorization": f"Bearer {_TRADIER_TOKEN}",
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        raw = resp.get("quotes", {}).get("quote", [])
        if isinstance(raw, dict):
            raw = [raw]
        return {q["symbol"]: float(q.get("last") or q.get("close") or 0)
                for q in raw if q.get("symbol") and (q.get("last") or q.get("close"))}
    except Exception as e:
        print(f"[alert_grading] td_quotes error (non-fatal): {e}")
        return {}


def _nearest_close_prices(cu, target_date, tickers):
    """polygon_market_daily lookup: nearest scan_date >= target_date, mirrors
    aiem_process.aiem_grade_t3_t5()'s pattern exactly."""
    if not tickers:
        return {}
    try:
        cu.execute(
            "SELECT DISTINCT scan_date FROM polygon_market_daily "
            "WHERE scan_date >= %s ORDER BY scan_date ASC LIMIT 1",
            (target_date,),
        )
        row = cu.fetchone()
        if not row:
            return {}
        snap_date = row[0]
        cu.execute(
            "SELECT ticker, close_price FROM polygon_market_daily "
            "WHERE scan_date = %s AND ticker = ANY(%s)",
            (snap_date, tickers),
        )
        return {r[0]: float(r[1]) for r in cu.fetchall()}
    except Exception as e:
        print(f"[alert_grading] nearest_close lookup error (non-fatal): {e}")
        return {}


def _grade_one_horizon(conn, days_offset, pct_col):
    """Fills one outcome column (d1/d3/d5) for every eligible SIGNAL row.
    Returns list of (id, signal_source, pct) for rows just filled at the
    decision horizon, so the caller can update trust weights afterward."""
    cu = conn.cursor()
    just_decided = []
    try:
        cu.execute(f"""
            SELECT id, ticker, trigger_price, signal_source, sent_at::date AS alert_date
            FROM telegram_alert_ledger
            WHERE alert_class = 'SIGNAL' AND is_test = FALSE
              AND ticker IS NOT NULL AND trigger_price IS NOT NULL
              AND {pct_col} IS NULL
              AND (sent_at::date + %s) <= CURRENT_DATE
            ORDER BY sent_at
            LIMIT 500
        """, (timedelta(days=days_offset),))
        rows = cu.fetchall()
    except Exception as e:
        print(f"[alert_grading] pending-rows query error ({pct_col}, non-fatal): {e}")
        return just_decided

    if not rows:
        return just_decided

    by_target_date = {}
    for row_id, ticker, trigger_price, signal_source, alert_date in rows:
        target = alert_date + timedelta(days=days_offset)
        by_target_date.setdefault(target, []).append(
            (row_id, ticker, float(trigger_price), signal_source)
        )

    for target_date, group in by_target_date.items():
        tickers_needed = sorted({t for _, t, _, _ in group})
        price_map = _nearest_close_prices(cu, target_date, tickers_needed)

        missing = [t for t in tickers_needed if t not in price_map]
        if missing:
            price_map.update(_td_quotes(missing))

        for row_id, ticker, trigger_price, signal_source in group:
            price = price_map.get(ticker)
            if not price or not trigger_price:
                continue
            try:
                pct = round((price - trigger_price) / trigger_price * 100, 4)
                if pct_col == _DECISION_HORIZON_COL:
                    win_loss = "WIN" if pct > 0 else "LOSS"
                    cu.execute(f"""
                        UPDATE telegram_alert_ledger
                        SET {pct_col} = %s, win_loss = %s, graded = TRUE, graded_at = NOW()
                        WHERE id = %s AND graded = FALSE
                    """, (pct, win_loss, row_id))
                    if cu.rowcount:
                        just_decided.append((row_id, signal_source, pct))
                else:
                    cu.execute(f"""
                        UPDATE telegram_alert_ledger SET {pct_col} = %s WHERE id = %s
                    """, (pct, row_id))
            except Exception as e:
                print(f"[alert_grading] update error id={row_id} {ticker} (non-fatal): {e}")

    conn.commit()
    return just_decided


def run_daily_grading():
    """Entry point for the daily scheduled job. Fails open: any exception
    is caught and logged, never raised to the caller/scheduler."""
    conn = None
    total_graded = 0
    total_trust_updates = 0
    try:
        conn = _db()
        for days_offset, pct_col in _HORIZONS:
            decided = _grade_one_horizon(conn, days_offset, pct_col)
            total_graded += len(decided) if pct_col == _DECISION_HORIZON_COL else 0
            for row_id, signal_source, pct in decided:
                try:
                    mlst.update_trust_weight(
                        signal_name=signal_source,
                        context_bucket=_CONTEXT_BUCKET,
                        new_outcome_was_win=(pct > 0),
                    )
                    total_trust_updates += 1
                except Exception as e:
                    print(f"[alert_grading] trust update error id={row_id} "
                          f"source={signal_source} (non-fatal): {e}")
        print(f"[alert_grading] run complete — {total_graded} alerts newly graded, "
              f"{total_trust_updates} trust-weight updates")
    except Exception as e:
        print(f"[alert_grading] run_daily_grading error (non-fatal): {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _digest_ledger_stats(conn, lookback_days: int):
    """Per-source stats over the lookback window: alerts sent, n graded,
    win count, avg D+3 outcome. Only SIGNAL-class, non-test rows count."""
    cu = conn.cursor()
    try:
        cu.execute(
            """
            SELECT signal_source,
                   COUNT(*) AS sent_7d,
                   COUNT(*) FILTER (WHERE graded) AS graded_7d,
                   COUNT(*) FILTER (WHERE win_loss = 'WIN') AS wins_7d,
                   AVG(outcome_d3_pct) FILTER (WHERE outcome_d3_pct IS NOT NULL) AS avg_d3_pct
            FROM telegram_alert_ledger
            WHERE alert_class = 'SIGNAL' AND is_test = FALSE
              AND signal_source != 'unclassified'
              AND sent_at >= NOW() - (%s || ' days')::interval
            GROUP BY signal_source
            """,
            (lookback_days,),
        )
        return {
            r[0]: {
                "sent_7d": r[1],
                "graded_7d": r[2],
                "wins_7d": r[3],
                "avg_d3_pct": float(r[4]) if r[4] is not None else None,
            }
            for r in cu.fetchall()
        }
    except Exception as e:
        print(f"[alert_grading] digest ledger-stats query error (non-fatal): {e}")
        return {}


def _digest_trend_arrow(signal_source: str, current_weight: float, lookback_days: int) -> str:
    """Compares current trust_weight to the oldest signal_trust_history row
    within the lookback window for this signal (TELEGRAM_ALERTS bucket)."""
    try:
        history = mlst.get_trust_history(signal_source, _CONTEXT_BUCKET, limit=500)
        if not history:
            return ""
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        older = [h for h in history if h["recorded_at"] <= cutoff]
        baseline = older[0] if older else history[-1]
        baseline_weight = float(baseline["trust_weight"])
        if current_weight > baseline_weight + 0.02:
            return " \u2191"
        if current_weight < baseline_weight - 0.02:
            return " \u2193"
        return " \u2192"
    except Exception as e:
        print(f"[alert_grading] digest trend-arrow error for {signal_source} (non-fatal): {e}")
        return ""


def build_weekly_digest(lookback_days: int = 7):
    """Builds the Friday weekly Telegram-alert trust digest text, or None
    if there is nothing to report yet (no SIGNAL alerts logged at all).

    Purely informational — Phase 4 soft-gate visibility only. Does not
    touch alert_class, does not suppress anything, and this function
    cannot fail the caller: any internal error is caught and returns None
    so a bad digest run just means "no digest sent this week", never a
    crash of the scheduler thread that calls it.
    """
    conn = None
    try:
        conn = _db()
        cu = conn.cursor()
        cu.execute(
            "SELECT COUNT(*) FROM telegram_alert_ledger "
            "WHERE alert_class = 'SIGNAL' AND is_test = FALSE"
        )
        (total_signal_rows,) = cu.fetchone()
        if not total_signal_rows:
            return None

        ledger_stats = _digest_ledger_stats(conn, lookback_days)
        trust_rows = mlst.get_current_trust_weights(_CONTEXT_BUCKET)
        trust_by_source = {r["signal_name"]: r for r in trust_rows}

        sources = sorted(
            set(ledger_stats) | set(trust_by_source),
            key=lambda s: float(trust_by_source.get(s, {}).get("trust_weight", 1.0)),
            reverse=True,
        )[:15]

        if not sources:
            return None

        lines = [
            "\U0001F4CA Weekly Alert Trust Digest",
            "(informational — no gating active; Phase 5 hard-gating requires separate approval)",
            "",
        ]
        for source in sources:
            stats = ledger_stats.get(source, {})
            trust = trust_by_source.get(source)
            sent_7d = stats.get("sent_7d", 0)
            graded_7d = stats.get("graded_7d", 0)
            wins_7d = stats.get("wins_7d", 0)
            avg_d3 = stats.get("avg_d3_pct")
            wr_7d = f"{(wins_7d / graded_7d * 100):.0f}%" if graded_7d else "n/a"
            avg_d3_str = f"{avg_d3:+.2f}%" if avg_d3 is not None else "n/a"

            if trust:
                weight = float(trust["trust_weight"])
                n_total = int(trust["n_outcomes_observed"])
                wr_total = float(trust["rolling_win_rate"]) * 100
                trend = _digest_trend_arrow(source, weight, lookback_days)
                readiness = ("\u2705 ready for Phase 5 review" if n_total >= 20
                             else f"{n_total}/20 toward Phase 5 review threshold")
                lines.append(
                    f"\u2022 {source}: weight {weight:.2f}{trend} · all-time WR {wr_total:.0f}% (n={n_total})\n"
                    f"   7d: {sent_7d} signals, {graded_7d} graded, {wr_7d} WR, avg D+3 {avg_d3_str}\n"
                    f"   {readiness}"
                )
            else:
                lines.append(
                    f"\u2022 {source}: not yet trust-scored (no graded outcomes)\n"
                    f"   7d: {sent_7d} signals, {graded_7d} graded, {wr_7d} WR, avg D+3 {avg_d3_str}"
                )

        return "\n".join(lines)
    except Exception as e:
        print(f"[alert_grading] build_weekly_digest error (non-fatal): {e}")
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    run_daily_grading()
