"""
decision_logging_helper.py
====================================================================
Reusable helper so every signal module logs a REAL, per-case reasoning
string to agent_decisions — built from actual computed values, not a
generic template.

Each function wraps dl.log_decision() in a try/except so a logging
failure NEVER propagates into the scanner that called it.
====================================================================
"""

import decision_logger as dl


def log_gamma_decision(ticker: str, fir: float, vol_oi: float,
                       score: float, price_change_pct: float,
                       top_strike):
    """Log a gamma pressure scan signal fire to agent_decisions."""
    try:
        strike_str = f"${top_strike:.0f}" if top_strike else "near-ATM"
        direction  = "+" if price_change_pct >= 0 else ""
        reasoning = (
            f"Gamma pressure scan flagged {ticker}: Float Impact Ratio {fir:.2f}% "
            f"(dealer forced-share demand as % of float), Vol/OI {vol_oi:.1f}x "
            f"(fresh call buying relative to existing open interest), "
            f"concentrated near the {strike_str} strike. "
            f"Price {direction}{price_change_pct:.1f}% today — dealer delta-hedging of "
            f"fresh call volume is creating forced share demand. "
            f"Composite score: {score:.1f}."
        )
        dl.log_decision(
            signal_name="gamma_pressure_scan",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=round(min(0.92, 0.50 + fir * 0.08), 2),
        )
    except Exception as _exc:
        print(f"[log_gamma_decision] {type(_exc).__name__}: {_exc}")


def log_charm_decision(ticker: str, strike: float, expiry: str,
                       days_out: int, oi: int, otm_pct: float,
                       charm_score: float):
    """Log a charm cascade signal fire to agent_decisions."""
    try:
        otm_label = f"{abs(otm_pct):.1f}% {'OTM' if otm_pct > 0 else 'ITM'}"
        reasoning = (
            f"Charm cascade flagged {ticker}: ${strike:.0f} strike expiring {expiry} "
            f"({days_out}d out, {otm_label}, {oi:,} open interest). "
            f"At ≤10 days to expiry in this strike zone, charm (dDelta/dTime) is near "
            f"its maximum — dealer hedge ratios rise automatically each calendar day "
            f"even without price movement, creating deterministic forced buying. "
            f"Charm score: {charm_score:.1f}."
        )
        dl.log_decision(
            signal_name="charm_cascade",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=round(min(0.88, 0.40 + min(charm_score, 200) * 0.002), 2),
        )
    except Exception as _exc:
        print(f"[log_charm_decision] {type(_exc).__name__}: {_exc}")


def log_dark_pool_decision(ticker: str, off_exchange_pct: float, volume: int):
    """Log a dark pool convergence signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      dp_pct>=60% (pts=2.0) -> 0.85
      dp_pct>=50% (pts=1.5) -> 0.72
      dp_pct>=40% (pts=1.0) -> 0.60
    """
    try:
        pts = 2.0 if off_exchange_pct >= 60 else 1.5 if off_exchange_pct >= 50 else 1.0
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        reasoning = (
            f"Dark pool scanner flagged {ticker}: {off_exchange_pct:.1f}% of "
            f"{volume:,} shares traded were routed off-exchange (FINRA Reg SHO data). "
            f"Institutions routing block orders through dark pools to avoid market "
            f"impact on lit exchanges — consistent with stealth accumulation "
            f"ahead of a directional move. "
            f"Layer 5 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="dark_pool_scanner",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_dark_pool_decision] {type(_exc).__name__}: {_exc}")


def log_unusual_calls_decision(ticker: str, call_volume: int, oi: int,
                                vol_oi: float, strike: float,
                                expiry: str, prem: int, otm_pct: float):
    """Log an unusual call activity signal fire to agent_decisions."""
    try:
        if prem >= 1_000_000:
            prem_str = f"${prem / 1_000_000:.1f}M"
        elif prem >= 1_000:
            prem_str = f"${prem / 1_000:.0f}K"
        else:
            prem_str = f"${prem}"
        oi_str  = f"{oi:,}" if oi > 0 else "unsettled (intraday)"
        voi_str = f"{vol_oi:.1f}x" if vol_oi > 0 else "N/A (no prior OI)"
        otm_str = (f"{otm_pct:.1f}% OTM" if otm_pct > 0
                   else f"{abs(otm_pct):.1f}% ITM")
        reasoning = (
            f"Unusual call activity on {ticker}: {call_volume:,} contracts "
            f"({prem_str} premium) at the ${strike:.0f} strike expiring {expiry} "
            f"({otm_str}), against {oi_str} existing open interest "
            f"(Vol/OI {voi_str}). Activity above typical levels for this name — "
            f"consistent with institutional directional positioning or "
            f"event-driven call buying ahead of a catalyst."
        )
        dl.log_decision(
            signal_name="unusual_calls_scanner",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=round(min(0.90, 0.45 + min(vol_oi, 5) * 0.06), 2),
        )
    except Exception as _exc:
        print(f"[log_unusual_calls_decision] {type(_exc).__name__}: {_exc}")


def log_far_otm_sweep_decision(ticker: str, vol_oi: float, prem: int,
                               otm_pct: float, strike: float, expiry: str,
                               cap_tier: str, pts: float):
    """Log a far-OTM sweep signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      vol_oi>=10 (pts=2.0) -> 0.85
      vol_oi>=7  (pts=1.5) -> 0.72
      vol_oi>=5  (pts=1.0) -> 0.60   (SQL floor from _get_far_otm_sweeps)
    """
    try:
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        reasoning = (
            f"Far-OTM sweep flagged {ticker}: vol/OI ratio {vol_oi:.1f}x on "
            f"${strike:.2f} strike (exp {expiry}, {otm_pct:.0f}% OTM), "
            f"premium ${prem:,}. "
            f"High vol/OI on far-OTM calls signals directional conviction — "
            f"buyers opening new positions rather than closing hedges — "
            f"consistent with informed positioning ahead of a catalyst "
            f"({cap_tier} cap). Layer 7 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="far_otm_sweep",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_far_otm_sweep_decision] {type(_exc).__name__}: {_exc}")


def log_float_pressure_decision(ticker: str, pressure_pct: float,
                                float_m: float, call_oi: int, pts: float):
    """Log a float-adjusted options demand signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      pressure_pct>=8.0% (pts=2.0) -> 0.85
      pressure_pct>=4.0% (pts=1.5) -> 0.72
      pressure_pct>=2.0% (pts=1.0) -> 0.60
    """
    try:
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        reasoning = (
            f"Float-adjusted options demand flagged {ticker}: {pressure_pct:.2f}% of "
            f"the {float_m:.1f}M-share float is tied up in MM delta obligations "
            f"({call_oi:,} call OI × 100 shares × avg delta 0.40). "
            f"On micro-float stocks, even modest call OI forces MMs to buy a "
            f"meaningful percentage of the entire float as a delta hedge — creating "
            f"a self-reinforcing feedback loop where price rises force additional "
            f"buying. Layer 6 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="float_pressure",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_float_pressure_decision] {type(_exc).__name__}: {_exc}")


def log_short_interest_decision(ticker: str, si_pct: float, dtc: float, pts: float):
    """Log a short interest overlay signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec (line 14291
    of main.py) — no invented coefficient:
      pts=2.0 (si_pct>=20%) -> 0.85
      pts=1.5 (si_pct>=15%) -> 0.72
      pts=1.0 (si_pct>=8%)  -> 0.60
    """
    try:
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.50)
        reasoning = (
            f"Short interest overlay flagged {ticker}: {si_pct:.1f}% of float is short "
            f"({dtc:.1f} days-to-cover, FINRA data via Finviz). "
            f"Significant short interest creates forced-covering pressure on any "
            f"sustained upward price move — dealer and short-seller buying amplifies "
            f"directional moves beyond what fundamental demand alone would produce. "
            f"Layer 4 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="short_interest",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_short_interest_decision] {type(_exc).__name__}: {_exc}")
