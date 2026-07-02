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
    """Log a dark pool convergence signal fire to agent_decisions."""
    try:
        reasoning = (
            f"Dark pool scanner flagged {ticker}: {off_exchange_pct:.1f}% of "
            f"{volume:,} shares traded were routed off-exchange (FINRA Reg SHO data), "
            f"above the 45% institutional accumulation threshold. "
            f"Institutions routing block orders through dark pools to avoid market "
            f"impact on lit exchanges — consistent with stealth accumulation "
            f"ahead of a directional move."
        )
        dl.log_decision(
            signal_name="dark_pool_scanner",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=round(min(0.85, 0.40 + off_exchange_pct * 0.006), 2),
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
