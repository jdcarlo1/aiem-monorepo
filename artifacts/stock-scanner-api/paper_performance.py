"""
paper_performance.py — AIEM Paper Trading Performance Analytics
PERF-001 through PERF-041

Quant formulas and references stated inline per the quant-correctness rule.

Sharpe:  Sharpe (1994) "The Sharpe Ratio" JPIM Fall 1994
         S = mean(r)/std(r, ddof=1)  [per-trade, no time-series annualization]
Sortino: Sortino & van der Meer (1991) "Downside Risk" JPMR Fall 1991
         DD = sqrt(mean(min(r,0)^2))   Sort = mean(r)/DD
MaxDD:   de Prado (2018) "Advances in Financial ML" p.97
         DD = (equity - running_max) / running_max
Calmar:  Young (1991) "The Calmar Ratio" FMTRS Spring 1991
         C = total_return_pct / abs(max_dd_pct)
VaR:     Basel II (2004) §IV.A historical simulation
         VaR_95 = -np.percentile(returns, 5)
CVaR:    Acerbi & Tasche (2002) "On the coherence of Expected Shortfall"
         CVaR_95 = -mean(r[r <= -VaR_95])
"""

import os
import math
import numpy as np
import psycopg2
import datetime as _dt


_ACCOUNT_START = 20_000.0

_PROD_FILTER = """
    exit_price IS NOT NULL
    AND (is_test_data = FALSE OR is_test_data IS NULL)
    AND ticker != 'DEDUP_TEST'
    AND trade_date < '2027-01-01'
"""


def _fetch_closed(cur, window_days=None):
    date_filter = ""
    params = []
    if window_days:
        date_filter = " AND trade_date >= CURRENT_DATE - (%s * INTERVAL '1 day')"
        params.append(window_days)

    cur.execute(f"""
        SELECT id, ticker, trade_type, direction, signal_source,
               entry_price, exit_price, quantity, notional, pnl, pnl_pct,
               trade_date, exit_date, entry_score,
               EXTRACT(EPOCH FROM (exit_date::timestamp - trade_date::timestamp))/86400.0
        FROM aiem_paper_trades
        WHERE {_PROD_FILTER}
        {date_filter}
        ORDER BY exit_date ASC, id ASC
    """, params)
    cols = ['id','ticker','trade_type','direction','signal_source',
            'entry_price','exit_price','quantity','notional','pnl','pnl_pct',
            'trade_date','exit_date','entry_score','hold_days']
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fetch_open(cur):
    cur.execute("""
        SELECT id, ticker, trade_type, direction, signal_source,
               entry_price, last_price, quantity, notional, pnl, pnl_pct,
               trade_date, status
        FROM aiem_paper_trades
        WHERE exit_price IS NULL
          AND status = 'OPEN'
          AND (is_test_data = FALSE OR is_test_data IS NULL)
          AND ticker != 'DEDUP_TEST'
        ORDER BY trade_date ASC, id ASC
    """)
    cols = ['id','ticker','trade_type','direction','signal_source',
            'entry_price','last_price','quantity','notional','pnl','pnl_pct',
            'trade_date','status']
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def compute_paper_performance(db_url: str, window_days: int = None) -> dict:
    """
    Compute all PERF-001 through PERF-041 metrics from aiem_paper_trades.
    Returns a dict with all metrics, n counts, and diagnostic flags.
    """
    with psycopg2.connect(db_url, connect_timeout=5,
                          options="-c statement_timeout=8000") as conn, \
         conn.cursor() as cur:

        closed = _fetch_closed(cur, window_days)
        open_pos = _fetch_open(cur)

        n = len(closed)
        n_open = len(open_pos)

        pnls = [_safe_float(c['pnl'], 0.0) for c in closed]
        pnl_pcts = [_safe_float(c['pnl_pct']) for c in closed
                    if c['pnl_pct'] is not None]

        # ── Basic groupings ─────────────────────────────────────────────────
        wins     = [p for p in pnls if p > 0]
        losses   = [p for p in pnls if p < 0]
        bes      = [p for p in pnls if p == 0]

        gross_profit = sum(wins)
        gross_loss   = sum(abs(p) for p in losses)
        net_profit   = sum(pnls)

        open_unrealized = sum(_safe_float(o['pnl'], 0.0) for o in open_pos)
        total_pnl = net_profit + open_unrealized

        # ── PERF-008 total return ────────────────────────────────────────────
        total_return_pct = round(net_profit / _ACCOUNT_START * 100, 4)

        # ── PERF-009 annualized return ───────────────────────────────────────
        # Compute from date range; flagged insufficient if n<20
        if closed:
            dates = sorted([str(c['exit_date']) for c in closed if c['exit_date']])
            try:
                first = _dt.date.fromisoformat(dates[0])
                last  = _dt.date.fromisoformat(dates[-1])
                cal_days = max((last - first).days, 1)
                years = cal_days / 365.25
                ann_return_pct = round(
                    ((1 + net_profit / _ACCOUNT_START) ** (1.0 / years) - 1) * 100, 4
                ) if years > 0 else None
            except Exception:
                ann_return_pct = None
        else:
            ann_return_pct = None
        ann_insufficient = (n < 20)

        # ── PERF-010–012 rates ───────────────────────────────────────────────
        win_rate      = round(len(wins)/n*100, 4) if n else None
        loss_rate     = round(len(losses)/n*100, 4) if n else None
        be_rate       = round(len(bes)/n*100, 4) if n else None

        # ── PERF-013–016 averages and extremes ──────────────────────────────
        avg_win       = round(gross_profit/len(wins), 4) if wins else None
        avg_loss      = round(gross_loss/len(losses), 4) if losses else None
        largest_win   = round(max(wins), 4) if wins else None
        largest_loss  = round(min(losses), 4) if losses else None  # most negative

        # ── PERF-017 profit factor: gross_profit / gross_loss ───────────────
        profit_factor = round(gross_profit/gross_loss, 6) if gross_loss > 0 else None

        # ── PERF-018 payoff ratio: avg_win / avg_loss ───────────────────────
        payoff_ratio  = round(avg_win/avg_loss, 6) if (avg_win and avg_loss) else None

        # ── PERF-019 expected value per trade ───────────────────────────────
        ev_per_trade  = round(net_profit/n, 4) if n else None

        # ── PERF-020–023 drawdown ────────────────────────────────────────────
        equity_curve = [_ACCOUNT_START]
        for p in pnls:
            equity_curve.append(equity_curve[-1] + p)
        eq = np.array(equity_curve, dtype=float)
        running_max = np.maximum.accumulate(eq)
        dd_pct = (eq - running_max) / running_max * 100.0

        max_drawdown_pct   = round(float(dd_pct.min()), 4)
        current_dd_pct     = round(float(dd_pct[-1]), 4)

        # Drawdown duration: longest consecutive stretch below running peak (in trades)
        max_dd_dur = 0
        cur_dur    = 0
        for i, d in enumerate(dd_pct):
            if d < 0:
                cur_dur += 1
                max_dd_dur = max(max_dd_dur, cur_dur)
            else:
                cur_dur = 0

        # Recovery duration: number of trades since the bottom of the deepest drawdown
        dd_bottom_idx = int(np.argmin(dd_pct))
        recovered = any(dd_pct[j] >= 0 for j in range(dd_bottom_idx, len(dd_pct)))
        recovery_dur = None
        if recovered:
            for j in range(dd_bottom_idx, len(dd_pct)):
                if dd_pct[j] >= 0:
                    recovery_dur = j - dd_bottom_idx
                    break
        else:
            recovery_dur = len(dd_pct) - 1 - dd_bottom_idx  # still in drawdown

        # ── PERF-024–030 risk ratios ─────────────────────────────────────────
        # n<20 → statistically insufficient; values computed but flagged
        quant_insufficient = (n < 20)
        quant_note = (
            f"n={n} < 20: estimates have wide confidence intervals; "
            "meaningful only at n≥20 per standing protocol"
        ) if quant_insufficient else None

        if len(pnl_pcts) >= 2:
            arr = np.array(pnl_pcts, dtype=float)
            mu_r   = float(arr.mean())
            sig_r  = float(arr.std(ddof=1))

            # Sharpe (per-trade, no annualization — trades are not daily periods)
            sharpe = round(mu_r / sig_r, 6) if sig_r > 0 else None

            # Sortino (Sortino & van der Meer 1991, target=0)
            neg = np.minimum(arr, 0.0)
            dd_sort = math.sqrt(float((neg**2).mean()))
            sortino = round(mu_r / dd_sort, 6) if dd_sort > 0 else None

            # Volatility of returns
            vol_returns = round(sig_r, 4)

            # Downside deviation
            downside_dev = round(dd_sort, 4)

            # VaR 95% (historical, Basel II §IV.A)
            var_95 = round(-float(np.percentile(arr, 5.0)), 4)

            # CVaR 95% (Acerbi & Tasche 2002)
            below = arr[arr <= -var_95]
            cvar_95 = round(-float(below.mean()), 4) if len(below) > 0 else None

        else:
            sharpe = sortino = vol_returns = downside_dev = var_95 = cvar_95 = None

        # Calmar (Young 1991): total_return / abs(max_drawdown)
        calmar = (round(abs(total_return_pct / max_drawdown_pct), 6)
                  if max_drawdown_pct < 0 else None)

        # ── PERF-031 by ticker ───────────────────────────────────────────────
        by_ticker = {}
        for c in closed:
            t = c['ticker']
            if t not in by_ticker:
                by_ticker[t] = {'n':0,'wins':0,'losses':0,'breakevens':0,
                                'gross_profit':0.0,'gross_loss':0.0,'net_pnl':0.0}
            p = _safe_float(c['pnl'], 0.0)
            by_ticker[t]['n'] += 1
            by_ticker[t]['net_pnl'] += p
            if p > 0: by_ticker[t]['wins'] += 1; by_ticker[t]['gross_profit'] += p
            elif p < 0: by_ticker[t]['losses'] += 1; by_ticker[t]['gross_loss'] += abs(p)
            else: by_ticker[t]['breakevens'] += 1
        for t, d in by_ticker.items():
            d['net_pnl'] = round(d['net_pnl'], 4)
            d['win_rate'] = round(d['wins']/d['n']*100, 1) if d['n'] else None

        # ── PERF-032 by strategy (signal_source) ────────────────────────────
        by_strategy = {}
        for c in closed:
            s = (c['signal_source'] or 'unknown')[:40]
            if s not in by_strategy:
                by_strategy[s] = {'n':0,'wins':0,'net_pnl':0.0,'gross_profit':0.0,'gross_loss':0.0}
            p = _safe_float(c['pnl'], 0.0)
            by_strategy[s]['n'] += 1
            by_strategy[s]['net_pnl'] += p
            if p > 0: by_strategy[s]['wins'] += 1; by_strategy[s]['gross_profit'] += p
            elif p < 0: by_strategy[s]['gross_loss'] += abs(p)
        for s, d in by_strategy.items():
            d['net_pnl'] = round(d['net_pnl'], 4)
            d['win_rate'] = round(d['wins']/d['n']*100, 1) if d['n'] else None
            d['profit_factor'] = (round(d['gross_profit']/d['gross_loss'], 4)
                                  if d['gross_loss'] > 0 else None)

        # ── PERF-033 by strategy family (trade_type) ─────────────────────────
        by_strategy_family = {}
        for c in closed:
            tt = c['trade_type'] or 'UNKNOWN'
            if tt not in by_strategy_family:
                by_strategy_family[tt] = {'n':0,'wins':0,'net_pnl':0.0}
            p = _safe_float(c['pnl'], 0.0)
            by_strategy_family[tt]['n'] += 1
            by_strategy_family[tt]['net_pnl'] += p
            if p > 0: by_strategy_family[tt]['wins'] += 1
        for tt, d in by_strategy_family.items():
            d['net_pnl'] = round(d['net_pnl'], 4)
            d['win_rate'] = round(d['wins']/d['n']*100, 1) if d['n'] else None

        # ── PERF-034 by market regime — NOT stored in aiem_paper_trades ─────
        by_market_regime = None
        by_market_regime_note = (
            "market_regime column not present in aiem_paper_trades; "
            "oe_trade_records.regime exists but has only 2 closed rows (2026-07-23 test entries)"
        )

        # ── PERF-035 by volatility regime — NOT stored ───────────────────────
        by_vol_regime = None
        by_vol_regime_note = "volatility_regime not stored in aiem_paper_trades"

        # ── PERF-036 by sector — NOT stored ─────────────────────────────────
        by_sector = None
        by_sector_note = (
            "sector not stored in aiem_paper_trades; "
            "oe_trade_records.sector exists but has only 2 closed rows"
        )

        # ── PERF-037 by holding period ────────────────────────────────────────
        buckets = {
            'intraday (0d)':  [],
            '1 day':          [],
            '2–5 days':       [],
            '>5 days':        [],
        }
        for c in closed:
            hd = _safe_float(c['hold_days'], 0.0)
            p  = _safe_float(c['pnl'], 0.0)
            if hd < 0.5:          buckets['intraday (0d)'].append(p)
            elif hd < 1.5:        buckets['1 day'].append(p)
            elif hd <= 5.0:       buckets['2–5 days'].append(p)
            else:                 buckets['>5 days'].append(p)
        by_holding_period = {}
        for bk, ps in buckets.items():
            if ps:
                by_holding_period[bk] = {
                    'n': len(ps),
                    'net_pnl': round(sum(ps), 4),
                    'win_rate': round(sum(1 for p in ps if p > 0)/len(ps)*100, 1),
                }

        # ── PERF-038 by confidence band (entry_score, percentile-based) ──────
        # entry_score is RAW (not 0-100 normalized). Fixed 0/20/40/60/80/100
        # thresholds removed 2026-07-23 — they collapsed 8/9 trades into ">=80".
        # Bands are now quintile percentiles (P0-P20 … P80-P100) computed from
        # the actual score distribution at query time. Labels include the real
        # threshold values so callers can interpret them without knowing the scale.
        scored = [(float(c['entry_score']), float(c['pnl']))
                  for c in closed if c['entry_score'] is not None]
        by_confidence = {}
        by_confidence_note = (
            "Bands are percentile-based (P0-P20/P20-P40/P40-P60/P60-P80/P80-P100) "
            "computed from the actual entry_score distribution at query time. "
            "entry_score is raw (not 0-100 normalized); fixed thresholds removed 2026-07-23."
        )
        if len(scored) >= 2:
            _scores_only = [s for s, _ in scored]
            _pct_cuts = [float(np.percentile(_scores_only, p)) for p in (0, 20, 40, 60, 80, 100)]
            _band_defs = [
                (f"P0–P20 ({_pct_cuts[0]:.1f}–{_pct_cuts[1]:.1f})",   _pct_cuts[0], _pct_cuts[1], False),
                (f"P20–P40 ({_pct_cuts[1]:.1f}–{_pct_cuts[2]:.1f})",  _pct_cuts[1], _pct_cuts[2], False),
                (f"P40–P60 ({_pct_cuts[2]:.1f}–{_pct_cuts[3]:.1f})",  _pct_cuts[2], _pct_cuts[3], False),
                (f"P60–P80 ({_pct_cuts[3]:.1f}–{_pct_cuts[4]:.1f})",  _pct_cuts[3], _pct_cuts[4], False),
                (f"P80–P100 ({_pct_cuts[4]:.1f}–{_pct_cuts[5]:.1f})", _pct_cuts[4], _pct_cuts[5], True),
            ]
            for _lbl, _lo, _hi, _last in _band_defs:
                if _last:
                    _subset = [p for s, p in scored if s >= _lo]
                else:
                    _subset = [p for s, p in scored if _lo <= s < _hi]
                if _subset:
                    by_confidence[_lbl] = {
                        'n': len(_subset),
                        'net_pnl': round(sum(_subset), 4),
                        'win_rate': round(sum(1 for p in _subset if p > 0) / len(_subset) * 100, 1),
                        'threshold_lo': round(_lo, 4),
                        'threshold_hi': round(_hi, 4),
                    }
        elif len(scored) == 1:
            _s, _p = scored[0]
            by_confidence[f"P0–P100 ({_s:.1f})"] = {
                'n': 1, 'net_pnl': round(_p, 4),
                'win_rate': 100.0 if _p > 0 else 0.0,
                'threshold_lo': round(_s, 4), 'threshold_hi': round(_s, 4),
            }

        # ── PERF-039 by probability band — NOT stored ─────────────────────────
        by_prob_band = None
        by_prob_band_note = (
            "probability score not stored in aiem_paper_trades; "
            "entry_score field covers PERF-038 (confidence), "
            "no separate probability field exists"
        )

    return {
        # ── source metadata (PERF-001, 002, 003) ──────────────────────────────
        "data_source":              "aiem_paper_trades",
        "paper_trading_label":      "PAPER TRADING — SIMULATION ONLY",
        "live_trading_data":        None,
        "live_vs_paper_note":       "No live trading subsystem; paper is the only execution system",
        "verified_outcomes_filter": _PROD_FILTER.strip(),
        "dedup_test_excluded":      True,
        "future_date_excluded":     True,
        "is_test_data_filter":      "is_test_data=FALSE OR is_test_data IS NULL",

        # ── trade counts (PERF-004) ───────────────────────────────────────────
        "n_closed":                 n,
        "n_open":                   n_open,
        "open_unrealized_pnl":      round(open_unrealized, 4),

        # ── PERF-005 through PERF-007 ─────────────────────────────────────────
        "gross_profit":             round(gross_profit, 4),
        "gross_loss":               round(gross_loss, 4),
        "net_profit":               round(net_profit, 4),

        # ── PERF-008, 009 ─────────────────────────────────────────────────────
        "account_start":            _ACCOUNT_START,
        "total_return_pct":         total_return_pct,
        "annualized_return_pct":    ann_return_pct,
        "annualized_insufficient_n": ann_insufficient,

        # ── PERF-010, 011, 012 ────────────────────────────────────────────────
        "win_rate_pct":             win_rate,
        "loss_rate_pct":            loss_rate,
        "breakeven_rate_pct":       be_rate,
        "n_wins":                   len(wins),
        "n_losses":                 len(losses),
        "n_breakevens":             len(bes),

        # ── PERF-013, 014, 015, 016 ───────────────────────────────────────────
        "avg_winning_trade":        avg_win,
        "avg_losing_trade":         avg_loss,
        "largest_winning_trade":    largest_win,
        "largest_losing_trade":     largest_loss,

        # ── PERF-017, 018, 019 ────────────────────────────────────────────────
        "profit_factor":            profit_factor,
        "payoff_ratio":             payoff_ratio,
        "expected_value_per_trade": ev_per_trade,

        # ── PERF-020, 021, 022, 023 ───────────────────────────────────────────
        "max_drawdown_pct":         max_drawdown_pct,
        "current_drawdown_pct":     current_dd_pct,
        "drawdown_duration_trades": max_dd_dur,
        "recovery_duration_trades": recovery_dur,
        "equity_curve":             [round(e, 2) for e in equity_curve],

        # ── PERF-024 through PERF-030 ─────────────────────────────────────────
        "sharpe_per_trade":         sharpe,
        "sortino_per_trade":        sortino,
        "calmar_ratio":             calmar,
        "volatility_of_returns_pct": vol_returns,
        "downside_deviation_pct":   downside_dev,
        "var_95_pct":               var_95,
        "cvar_95_pct":              cvar_95,
        "quant_insufficient_n":     quant_insufficient,
        "quant_note":               quant_note,

        # ── PERF-031 through PERF-039 ─────────────────────────────────────────
        "by_ticker":                by_ticker,
        "by_strategy":              by_strategy,
        "by_strategy_family":       by_strategy_family,
        "by_market_regime":         by_market_regime,
        "by_market_regime_note":    by_market_regime_note,
        "by_vol_regime":            by_vol_regime,
        "by_vol_regime_note":       by_vol_regime_note,
        "by_sector":                by_sector,
        "by_sector_note":           by_sector_note,
        "by_holding_period":        by_holding_period,
        "by_confidence_band":       by_confidence,
        "by_confidence_band_note":  by_confidence_note,
        "by_prob_band":             by_prob_band,
        "by_prob_band_note":        by_prob_band_note,
    }
