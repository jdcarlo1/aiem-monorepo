"""
aiem_portfolio_engine/limits.py — S3+S7: Concentration Controls & Risk Budgets.

No new trade may bypass a hard concentration limit.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import datetime

from .snapshot import PortfolioSnapshot, PortfolioPosition
from .greeks import PortfolioGreeks
from .config import (
    PORTFOLIO_CAPITAL, CONTRACT_MULTIPLIER,
    MAX_TICKER_CONCENTRATION, MAX_SECTOR_CONCENTRATION,
    MAX_INDUSTRY_CONCENTRATION, MAX_STRATEGY_FAMILY_CONC,
    MAX_EXPIRATION_CONCENTRATION, MAX_STRIKE_AREA_CONC,
    MAX_BULLISH_CONCENTRATION, MAX_BEARISH_CONCENTRATION,
    MAX_LONG_VOL_CONCENTRATION, MAX_SHORT_VOL_CONCENTRATION, MAX_UNDEFINED_RISK_EXPOSURE,
    MAX_SIMULTANEOUS_POSITIONS, MAX_BUYING_POWER_UTILIZATION, MAX_PORTFOLIO_RISK_UTILIZATION,
    DAILY_LOSS_LIMIT, STRESS_TEST_LOSS_LIMIT, LIQUIDITY_ADJ_LOSS_LIMIT,
    MAX_PORTFOLIO_DELTA, MAX_PORTFOLIO_GAMMA, MAX_PORTFOLIO_VEGA, MAX_PORTFOLIO_THETA,
    INDUSTRY_GROUPS,
)


@dataclass
class ConcentrationBreach:
    limit_name:    str
    current_value: float
    limit_value:   float
    details:       str


@dataclass
class ConcentrationResult:
    ticker_pct:               float
    sector_pct:               float
    industry_pct:             float
    strike_pct:               float
    strategy_family_pct:      float
    expiration_pct:           float
    directional_bullish_pct:  float
    directional_bearish_pct:  float
    long_vol_pct:             float
    short_vol_pct:            float
    n_positions_after:        int
    buying_power_utilization: float
    risk_utilization:         float
    breaches:                 List[ConcentrationBreach] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker_pct":                round(self.ticker_pct, 4),
            "sector_pct":                round(self.sector_pct, 4),
            "industry_pct":              round(self.industry_pct, 4),
            "strike_pct":                round(self.strike_pct, 4),
            "strategy_family_pct":       round(self.strategy_family_pct, 4),
            "expiration_pct":            round(self.expiration_pct, 4),
            "directional_bullish_pct":   round(self.directional_bullish_pct, 4),
            "directional_bearish_pct":   round(self.directional_bearish_pct, 4),
            "long_vol_pct":              round(self.long_vol_pct, 4),
            "short_vol_pct":             round(self.short_vol_pct, 4),
            "n_positions_after":         self.n_positions_after,
            "buying_power_utilization":  round(self.buying_power_utilization, 4),
            "risk_utilization":          round(self.risk_utilization, 4),
            "n_breaches":                len(self.breaches),
            "breaches": [
                {"limit": b.limit_name, "value": round(b.current_value, 4),
                 "limit_val": round(b.limit_value, 4), "details": b.details}
                for b in self.breaches
            ],
        }


def _expiry_week(exp_str: Optional[str]) -> Optional[str]:
    """Return ISO year-week string for an expiration date string."""
    if not exp_str:
        return None
    try:
        d = datetime.date.fromisoformat(str(exp_str))
        return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
    except Exception:
        return None


def check_concentration(
    snapshot: PortfolioSnapshot,
    candidate_ticker: str,
    candidate_capital: float,
    candidate_strategy_name: str,
    candidate_strategy_family: Optional[str],
    candidate_direction: Optional[str],
    candidate_is_long_vol: bool,
    candidate_is_short_vol: bool,
    candidate_expiry: Optional[str],
    candidate_sector: Optional[str],
    candidate_is_undefined_risk: bool = False,
    candidate_industry: Optional[str] = None,
    candidate_strike: Optional[float] = None,
) -> ConcentrationResult:
    """
    Compute all concentration metrics after adding the candidate position.
    Returns ConcentrationResult with breaches list.
    """
    breaches: List[ConcentrationBreach] = []

    all_positions = list(snapshot.positions)

    # ── Ticker concentration ──────────────────────────────────────────────────
    ticker_capital = sum(
        p.capital_at_risk for p in all_positions if p.ticker == candidate_ticker
    ) + candidate_capital
    ticker_pct = ticker_capital / max(PORTFOLIO_CAPITAL, 1)
    if ticker_pct > MAX_TICKER_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_TICKER_CONCENTRATION", ticker_pct, MAX_TICKER_CONCENTRATION,
            f"{candidate_ticker}: {ticker_pct:.1%} > {MAX_TICKER_CONCENTRATION:.1%}",
        ))

    # ── Sector concentration ──────────────────────────────────────────────────
    cand_sector = candidate_sector
    sector_capital = sum(
        p.capital_at_risk for p in all_positions if p.sector == cand_sector and cand_sector
    ) + (candidate_capital if cand_sector else 0)
    sector_pct = sector_capital / max(PORTFOLIO_CAPITAL, 1) if cand_sector else 0.0
    if cand_sector and sector_pct > MAX_SECTOR_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_SECTOR_CONCENTRATION", sector_pct, MAX_SECTOR_CONCENTRATION,
            f"sector={cand_sector}: {sector_pct:.1%} > {MAX_SECTOR_CONCENTRATION:.1%}",
        ))

    # ── Industry concentration (finer-grained than sector) ────────────────────
    cand_industry_group = candidate_industry
    if cand_industry_group is None:
        for grp_name, grp_set in INDUSTRY_GROUPS.items():
            if candidate_ticker.upper() in grp_set:
                cand_industry_group = grp_name
                break
    industry_pct = 0.0
    if cand_industry_group:
        industry_set = INDUSTRY_GROUPS.get(cand_industry_group, set())
        industry_capital = sum(
            p.capital_at_risk for p in all_positions if p.ticker.upper() in industry_set
        ) + candidate_capital
        industry_pct = industry_capital / max(PORTFOLIO_CAPITAL, 1)
        if industry_pct > MAX_INDUSTRY_CONCENTRATION:
            breaches.append(ConcentrationBreach(
                "MAX_INDUSTRY_CONCENTRATION", industry_pct, MAX_INDUSTRY_CONCENTRATION,
                f"industry={cand_industry_group}: {industry_pct:.1%} > {MAX_INDUSTRY_CONCENTRATION:.1%}",
            ))

    # ── Strike-area concentration (±5% of candidate strike, same underlying) ─
    strike_pct = 0.0
    if candidate_strike is not None and candidate_strike > 0:
        strike_low  = candidate_strike * 0.95
        strike_high = candidate_strike * 1.05
        strike_capital = sum(
            p.capital_at_risk for p in all_positions
            if p.ticker == candidate_ticker and any(
                lg.strike is not None and strike_low <= lg.strike <= strike_high
                for lg in p.legs
            )
        ) + candidate_capital
        strike_pct = strike_capital / max(PORTFOLIO_CAPITAL, 1)
        if strike_pct > MAX_STRIKE_AREA_CONC:
            breaches.append(ConcentrationBreach(
                "MAX_STRIKE_AREA_CONC", strike_pct, MAX_STRIKE_AREA_CONC,
                f"{candidate_ticker} strike±5% area: {strike_pct:.1%} > {MAX_STRIKE_AREA_CONC:.1%}",
            ))

    # ── Strategy-family concentration ─────────────────────────────────────────
    cand_fam = (candidate_strategy_family or candidate_strategy_name or "").upper()
    fam_capital = sum(
        p.capital_at_risk for p in all_positions
        if (p.strategy_family or p.strategy_name or "").upper() == cand_fam and cand_fam
    ) + candidate_capital
    fam_pct = fam_capital / max(PORTFOLIO_CAPITAL, 1) if cand_fam else 0.0
    if cand_fam and fam_pct > MAX_STRATEGY_FAMILY_CONC:
        breaches.append(ConcentrationBreach(
            "MAX_STRATEGY_FAMILY_CONC", fam_pct, MAX_STRATEGY_FAMILY_CONC,
            f"family={cand_fam}: {fam_pct:.1%} > {MAX_STRATEGY_FAMILY_CONC:.1%}",
        ))

    # ── Expiration concentration ──────────────────────────────────────────────
    cand_week = _expiry_week(candidate_expiry)
    exp_capital = 0.0
    if cand_week:
        for p in all_positions:
            for lg in p.legs:
                if _expiry_week(lg.expiration) == cand_week:
                    exp_capital += p.capital_at_risk
                    break
        exp_capital += candidate_capital
    exp_pct = exp_capital / max(PORTFOLIO_CAPITAL, 1) if cand_week else 0.0
    if cand_week and exp_pct > MAX_EXPIRATION_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_EXPIRATION_CONCENTRATION", exp_pct, MAX_EXPIRATION_CONCENTRATION,
            f"week={cand_week}: {exp_pct:.1%} > {MAX_EXPIRATION_CONCENTRATION:.1%}",
        ))

    # ── Directional concentration ─────────────────────────────────────────────
    bull_dir = ("BULLISH", "BULL", "CALL", "LONG_CALL", "DEBIT_SPREAD")
    bear_dir = ("BEARISH", "BEAR", "PUT", "LONG_PUT", "CREDIT_SPREAD")
    bull_cap = sum(
        p.capital_at_risk for p in all_positions
        if (p.direction or p.thesis or "").upper() in bull_dir
    ) + (candidate_capital if (candidate_direction or "").upper() in bull_dir else 0)
    bear_cap = sum(
        p.capital_at_risk for p in all_positions
        if (p.direction or p.thesis or "").upper() in bear_dir
    ) + (candidate_capital if (candidate_direction or "").upper() in bear_dir else 0)
    bull_pct = bull_cap / max(PORTFOLIO_CAPITAL, 1)
    bear_pct = bear_cap / max(PORTFOLIO_CAPITAL, 1)
    if bull_pct > MAX_BULLISH_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_BULLISH_CONCENTRATION", bull_pct, MAX_BULLISH_CONCENTRATION,
            f"bullish capital: {bull_pct:.1%} > {MAX_BULLISH_CONCENTRATION:.1%}",
        ))
    if bear_pct > MAX_BEARISH_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_BEARISH_CONCENTRATION", bear_pct, MAX_BEARISH_CONCENTRATION,
            f"bearish capital: {bear_pct:.1%} > {MAX_BEARISH_CONCENTRATION:.1%}",
        ))

    # ── Long/short volatility concentration ──────────────────────────────────
    lv_cap = sum(
        p.capital_at_risk for p in all_positions if p.is_long_vol
    ) + (candidate_capital if candidate_is_long_vol else 0)
    sv_cap = sum(
        p.capital_at_risk for p in all_positions if p.is_short_vol
    ) + (candidate_capital if candidate_is_short_vol else 0)
    lv_pct = lv_cap / max(PORTFOLIO_CAPITAL, 1)
    sv_pct = sv_cap / max(PORTFOLIO_CAPITAL, 1)
    if lv_pct > MAX_LONG_VOL_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_LONG_VOL_CONCENTRATION", lv_pct, MAX_LONG_VOL_CONCENTRATION,
            f"long-vol capital: {lv_pct:.1%} > {MAX_LONG_VOL_CONCENTRATION:.1%}",
        ))
    if sv_pct > MAX_SHORT_VOL_CONCENTRATION:
        breaches.append(ConcentrationBreach(
            "MAX_SHORT_VOL_CONCENTRATION", sv_pct, MAX_SHORT_VOL_CONCENTRATION,
            f"short-vol capital: {sv_pct:.1%} > {MAX_SHORT_VOL_CONCENTRATION:.1%}",
        ))

    # ── Undefined risk block ──────────────────────────────────────────────────
    if candidate_is_undefined_risk:
        breaches.append(ConcentrationBreach(
            "MAX_UNDEFINED_RISK_EXPOSURE", 1.0, 0.0,
            "undefined-risk strategies blocked in paper trading",
        ))

    # ── Position count ────────────────────────────────────────────────────────
    n_after = snapshot.n_open_positions + 1
    if n_after > MAX_SIMULTANEOUS_POSITIONS:
        breaches.append(ConcentrationBreach(
            "MAX_SIMULTANEOUS_POSITIONS", float(n_after), float(MAX_SIMULTANEOUS_POSITIONS),
            f"{n_after} positions > {MAX_SIMULTANEOUS_POSITIONS} limit",
        ))

    # ── Buying power utilization ──────────────────────────────────────────────
    bp_used = (snapshot.committed_capital + candidate_capital) / max(PORTFOLIO_CAPITAL, 1)
    if bp_used > MAX_BUYING_POWER_UTILIZATION:
        breaches.append(ConcentrationBreach(
            "MAX_BUYING_POWER_UTILIZATION", bp_used, MAX_BUYING_POWER_UTILIZATION,
            f"BP utilization: {bp_used:.1%} > {MAX_BUYING_POWER_UTILIZATION:.1%}",
        ))

    # ── Portfolio risk utilization ────────────────────────────────────────────
    max_loss_sum = sum(p.maximum_loss for p in all_positions) + candidate_capital
    risk_util = max_loss_sum / max(PORTFOLIO_CAPITAL, 1)
    if risk_util > MAX_PORTFOLIO_RISK_UTILIZATION:
        breaches.append(ConcentrationBreach(
            "MAX_PORTFOLIO_RISK_UTILIZATION", risk_util, MAX_PORTFOLIO_RISK_UTILIZATION,
            f"risk utilization: {risk_util:.1%} > {MAX_PORTFOLIO_RISK_UTILIZATION:.1%}",
        ))

    return ConcentrationResult(
        ticker_pct               = ticker_pct,
        sector_pct               = sector_pct,
        industry_pct             = industry_pct,
        strike_pct               = strike_pct,
        strategy_family_pct      = fam_pct,
        expiration_pct           = exp_pct,
        directional_bullish_pct  = bull_pct,
        directional_bearish_pct  = bear_pct,
        long_vol_pct             = lv_pct,
        short_vol_pct            = sv_pct,
        n_positions_after        = n_after,
        buying_power_utilization = bp_used,
        risk_utilization         = risk_util,
        breaches                 = breaches,
    )


@dataclass
class RiskBudget:
    daily_loss_remaining:       float
    portfolio_delta_remaining:  float
    portfolio_vega_remaining:   float
    portfolio_theta_floor_gap:  float
    buying_power_remaining:     float
    stress_loss_remaining:      float
    breaches:                   List[ConcentrationBreach] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_loss_remaining":      round(self.daily_loss_remaining, 2),
            "portfolio_delta_remaining": round(self.portfolio_delta_remaining, 4),
            "portfolio_vega_remaining":  round(self.portfolio_vega_remaining, 4),
            "portfolio_theta_floor_gap": round(self.portfolio_theta_floor_gap, 4),
            "buying_power_remaining":    round(self.buying_power_remaining, 2),
            "stress_loss_remaining":     round(self.stress_loss_remaining, 2),
            "n_breaches":                len(self.breaches),
            "breaches": [
                {"limit": b.limit_name, "value": round(b.current_value, 4),
                 "limit_val": round(b.limit_value, 4), "details": b.details}
                for b in self.breaches
            ],
        }


def check_risk_budget(
    snapshot:          PortfolioSnapshot,
    greeks_after:      PortfolioGreeks,
    worst_stress_loss: float = 0.0,
    daily_realized_pnl: float = 0.0,
) -> RiskBudget:
    """
    Check remaining risk budget across all budget dimensions.

    worst_stress_loss   : worst-case stress scenario P/L (negative = loss).
    daily_realized_pnl  : today's realized P&L on closed positions (negative = loss).
    """
    breaches: List[ConcentrationBreach] = []

    # ── Daily loss budget enforcement ─────────────────────────────────────────
    daily_loss_used = abs(min(daily_realized_pnl, 0.0))
    daily_loss_remaining = DAILY_LOSS_LIMIT - daily_loss_used
    if daily_loss_used > DAILY_LOSS_LIMIT:
        breaches.append(ConcentrationBreach(
            "DAILY_LOSS_LIMIT", daily_loss_used, DAILY_LOSS_LIMIT,
            f"daily realized loss ${daily_loss_used:.2f} exceeds daily limit ${DAILY_LOSS_LIMIT:.2f}",
        ))

    # ── Delta ─────────────────────────────────────────────────────────────────
    abs_delta = abs(greeks_after.total_delta)
    delta_remaining = MAX_PORTFOLIO_DELTA - abs_delta
    if abs_delta > MAX_PORTFOLIO_DELTA:
        breaches.append(ConcentrationBreach(
            "MAX_PORTFOLIO_DELTA", abs_delta, MAX_PORTFOLIO_DELTA,
            f"|delta|={abs_delta:.2f} > {MAX_PORTFOLIO_DELTA}",
        ))

    # ── Vega ──────────────────────────────────────────────────────────────────
    abs_vega = abs(greeks_after.vega)
    vega_remaining = MAX_PORTFOLIO_VEGA - abs_vega
    if abs_vega > MAX_PORTFOLIO_VEGA:
        breaches.append(ConcentrationBreach(
            "MAX_PORTFOLIO_VEGA", abs_vega, MAX_PORTFOLIO_VEGA,
            f"|vega|={abs_vega:.2f} > {MAX_PORTFOLIO_VEGA}",
        ))

    # ── Theta floor ───────────────────────────────────────────────────────────
    theta_gap = greeks_after.theta - MAX_PORTFOLIO_THETA
    if greeks_after.theta < MAX_PORTFOLIO_THETA:
        breaches.append(ConcentrationBreach(
            "MAX_PORTFOLIO_THETA", greeks_after.theta, MAX_PORTFOLIO_THETA,
            f"theta={greeks_after.theta:.2f} < floor {MAX_PORTFOLIO_THETA}",
        ))

    # ── Buying power ──────────────────────────────────────────────────────────
    bp_remaining = snapshot.cash_available
    if bp_remaining < 0:
        breaches.append(ConcentrationBreach(
            "BUYING_POWER_EXHAUSTED", abs(bp_remaining), 0.0,
            f"cash_available={bp_remaining:.2f} (negative)",
        ))

    # ── Stress loss ───────────────────────────────────────────────────────────
    stress_remaining = STRESS_TEST_LOSS_LIMIT - abs(worst_stress_loss)
    if abs(worst_stress_loss) > STRESS_TEST_LOSS_LIMIT:
        breaches.append(ConcentrationBreach(
            "STRESS_TEST_LOSS_LIMIT", abs(worst_stress_loss), STRESS_TEST_LOSS_LIMIT,
            f"worst stress loss=${abs(worst_stress_loss):.2f} > ${STRESS_TEST_LOSS_LIMIT:.2f}",
        ))

    return RiskBudget(
        daily_loss_remaining      = daily_loss_remaining,
        portfolio_delta_remaining = delta_remaining,
        portfolio_vega_remaining  = vega_remaining,
        portfolio_theta_floor_gap = theta_gap,
        buying_power_remaining    = bp_remaining,
        stress_loss_remaining     = stress_remaining,
        breaches                  = breaches,
    )
