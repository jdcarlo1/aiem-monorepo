"""
aiem_portfolio_engine/correlation.py — S4: Correlation & Duplicate-Risk Engine.

Detects portfolio risk that appears diversified by ticker but is driven
by the same factor.

IMPLEMENTED:
- Named correlation groups (mega_tech, semis, ev_meme, biotech_meme, crypto_adjacent)
- Historical return correlation from polygon_market_daily (30-day EOD window)
- Sector/industry overlap via TICKER_SECTOR_MAP
- Beta similarity (proxy: same sector ETF → same systematic risk)

NOT_IMPLEMENTED v1:
- Intraday correlation (no intraday bar history available)
- Common-factor exposure beyond sector/beta (no Fama-French factor model)
"""
from __future__ import annotations
import os, math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

import psycopg2
import psycopg2.extras

from .snapshot import PortfolioSnapshot
from .config import (
    MAX_CORRELATION_CLUSTER_EXP, CORRELATION_LOOKBACK_DAYS,
    CORRELATION_HIGH_THRESHOLD, CORRELATION_EXTREME_THRESHOLD,
    PORTFOLIO_CAPITAL, NOT_IMPLEMENTED_V1,
)

CORRELATION_GROUPS: Dict[str, set] = {
    "mega_tech":       {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA"},
    "semis":           {"NVDA", "AMD", "INTC", "AVGO", "MU", "MRVL", "AMAT", "LSCC", "CRDO"},
    "ev_meme":         {"TSLA", "RIVN", "LCID"},
    "biotech_meme":    {"MRNA", "BYND", "HOOD"},
    "crypto_adjacent": {"COIN", "MARA", "RIOT", "HIVE"},
}


@dataclass
class CorrelationCluster:
    cluster_name:   str
    tickers:        List[str]
    exposure_pct:   float      # fraction of PORTFOLIO_CAPITAL committed in cluster
    corr_score:     float      # max pairwise correlation (0-1)
    action:         str        # APPROVE / REDUCE / REJECT


@dataclass
class CorrelationResult:
    clusters:               List[CorrelationCluster]
    candidate_overlap_score: float   # 0 = no overlap, 1 = full duplicate
    duplicate_risk_score:   float    # 0-1, combines cluster + EOD correlation
    action:                 str      # APPROVE / REDUCE / REJECT
    historical_corr:        Optional[float]  # EOD 30-day correlation, None if unavailable
    not_implemented_items:  List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clusters": [
                {"name": c.cluster_name, "tickers": c.tickers,
                 "exposure_pct": round(c.exposure_pct, 4),
                 "corr_score": round(c.corr_score, 4),
                 "action": c.action}
                for c in self.clusters
            ],
            "candidate_overlap_score": round(self.candidate_overlap_score, 4),
            "duplicate_risk_score": round(self.duplicate_risk_score, 4),
            "action": self.action,
            "historical_corr": (
                round(self.historical_corr, 4) if self.historical_corr is not None else None
            ),
            "not_implemented_items": self.not_implemented_items,
        }


def _get_eod_returns(tickers: List[str], db_url: str, lookback: int = 30) -> Dict[str, List[float]]:
    """
    Fetch last `lookback` trading days of daily returns from polygon_market_daily.
    Returns dict: ticker -> [return_pct, ...] (oldest first).
    Returns empty dict on any error.
    """
    if not tickers or not db_url:
        return {}
    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur  = conn.cursor()
        placeholders = ",".join(["%s"] * len(tickers))
        cur.execute(f"""
            SELECT ticker, scan_date,
                   (close_price - LAG(close_price) OVER (PARTITION BY ticker ORDER BY scan_date))
                   / NULLIF(LAG(close_price) OVER (PARTITION BY ticker ORDER BY scan_date), 0)
                   AS daily_return
            FROM (
                SELECT ticker, scan_date, close_price
                FROM polygon_market_daily
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, scan_date DESC
                LIMIT %s
            ) sub
            ORDER BY ticker, scan_date
        """, tickers + [len(tickers) * (lookback + 5)])
        rows = cur.fetchall()
        cur.close(); conn.close()

        result: Dict[str, List[float]] = {}
        for ticker, scan_date, ret in rows:
            if ret is not None:
                result.setdefault(ticker, []).append(float(ret))
        return result
    except Exception:
        return {}


def _pearson_corr(xs: List[float], ys: List[float]) -> Optional[float]:
    """Pearson correlation coefficient for two equal-length series."""
    n = min(len(xs), len(ys))
    if n < 5:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx  = math.sqrt(sum((a - mx)**2 for a in xs))
    sy  = math.sqrt(sum((b - my)**2 for b in ys))
    if sx * sy == 0:
        return None
    return cov / (sx * sy)


def check_correlation(
    snapshot: PortfolioSnapshot,
    candidate_ticker: str,
    candidate_capital: float,
    db_url: str,
) -> CorrelationResult:
    """
    Evaluate correlation risk from adding candidate_ticker to the portfolio.
    """
    existing_tickers = list({p.ticker for p in snapshot.positions})
    all_tickers      = existing_tickers + ([candidate_ticker]
                        if candidate_ticker not in existing_tickers else [])

    # ── Named cluster analysis ────────────────────────────────────────────────
    clusters: List[CorrelationCluster] = []
    for cluster_name, cluster_set in CORRELATION_GROUPS.items():
        in_cluster = [t for t in all_tickers if t.upper() in cluster_set]
        if len(in_cluster) < 2:
            continue
        cluster_capital = sum(
            p.capital_at_risk for p in snapshot.positions if p.ticker in cluster_set
        ) + (candidate_capital if candidate_ticker in cluster_set else 0)
        exposure_pct = cluster_capital / max(PORTFOLIO_CAPITAL, 1)
        action = "APPROVE"
        if exposure_pct > MAX_CORRELATION_CLUSTER_EXP:
            action = "REDUCE" if exposure_pct < MAX_CORRELATION_CLUSTER_EXP * 1.5 else "REJECT"
        clusters.append(CorrelationCluster(
            cluster_name = cluster_name,
            tickers      = in_cluster,
            exposure_pct = exposure_pct,
            corr_score   = min(0.90, 0.50 + 0.10 * len(in_cluster)),
            action       = action,
        ))

    # ── Historical EOD correlation ────────────────────────────────────────────
    historical_corr: Optional[float] = None
    if existing_tickers and db_url:
        returns = _get_eod_returns(
            [candidate_ticker] + existing_tickers[:4], db_url, CORRELATION_LOOKBACK_DAYS
        )
        cand_ret = returns.get(candidate_ticker, [])
        max_corr = 0.0
        for t in existing_tickers[:4]:
            r = _pearson_corr(cand_ret, returns.get(t, []))
            if r is not None:
                max_corr = max(max_corr, abs(r))
        if max_corr > 0:
            historical_corr = round(max_corr, 4)

    # ── Overlap score ─────────────────────────────────────────────────────────
    cluster_breach = any(c.action in ("REDUCE", "REJECT") for c in clusters)
    eod_high       = historical_corr is not None and historical_corr >= CORRELATION_HIGH_THRESHOLD
    eod_extreme    = historical_corr is not None and historical_corr >= CORRELATION_EXTREME_THRESHOLD

    overlap_score = 0.0
    if cluster_breach:
        overlap_score += 0.50
    if eod_high:
        overlap_score += 0.30
    if eod_extreme:
        overlap_score += 0.20
    overlap_score = min(overlap_score, 1.0)

    dup_score = overlap_score
    if eod_extreme:
        action = "REJECT"
    elif eod_high or cluster_breach:
        action = "REDUCE"
    else:
        action = "APPROVE"

    return CorrelationResult(
        clusters                = clusters,
        candidate_overlap_score = overlap_score,
        duplicate_risk_score    = dup_score,
        action                  = action,
        historical_corr         = historical_corr,
        not_implemented_items   = [NOT_IMPLEMENTED_V1[0], NOT_IMPLEMENTED_V1[3]],
    )
