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


def log_stat_edge_decision(ticker: str, stat9_score: float, regime: str,
                           vpin: float, jump_detected: bool, source: str):
    """Log a Layer 9 Statistical Edge computation to agent_decisions.

    Confidence tiers are anchored to the semantic thresholds in the AI prompt spec
    (line 39657 of main.py) — no invented coefficient:
      stat9>=70 -> 0.85  ("strong statistical alignment")
      stat9>=50 -> 0.72  (above neutral midpoint of 0-100 scale)
      stat9< 50 -> 0.60  (below neutral; computed and relevant)

    Statistical basis note: the 6 component weights in _WEIGHTS (hurst 0.20,
    vpin 0.20, illiquidity 0.20, tail_risk 0.15, entropy 0.15, jump 0.10) are
    labeled "Tuned for the existing 8-layer universe" but have no cited empirical
    backtest. Flagged as design choices, not validated coefficients.
    """
    try:
        confidence = 0.85 if stat9_score >= 70 else 0.72 if stat9_score >= 50 else 0.60
        reasoning = (
            f"Layer 9 Statistical Edge score for {ticker}: {stat9_score:.1f}/100 "
            f"(source: {source}). "
            f"Regime={regime}, VPIN={vpin:.3f} (informed-flow toxicity; "
            f">=0.45=smart money positioning), jump_detected={jump_detected}. "
            f"Components: Hurst(regime fit 0.20wt) + VPIN(flow 0.20wt) + "
            f"Illiquidity(inverted 0.20wt) + Tail-risk(0.15wt) + "
            f"Entropy-clarity(0.15wt) + Jump-risk(0.10wt). "
            f"stat9>=70=strong alignment; stat9<40=edge unclear."
        )
        dl.log_decision(
            signal_name="stat_edge_signal",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_stat_edge_decision] {type(_exc).__name__}: {_exc}")


def log_sector_sympathy_decision(ticker: str, sector: str, heat_score: int,
                                 lead_tickers: list, pts: float):
    """Log a sector sympathy play signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      heat>=3 (pts=1.5) -> 0.85  (multiple lead tickers fired in sector)
      heat>=2 (pts=1.0) -> 0.72  (two leads)
      heat>=1 (pts=0.5) -> 0.60  (single lead — weakest L8 signal)
    """
    try:
        _CONFIDENCE_MAP = {1.5: 0.85, 1.0: 0.72, 0.5: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        leads_str = ", ".join(lead_tickers[:3])
        reasoning = (
            f"Sector sympathy flagged {ticker} in the {sector} sector: "
            f"{heat_score} lead ticker(s) ({leads_str}) fired unusual call activity "
            f"in the same sector within the past 2 days. "
            f"Hedge funds monitor sector momentum — when a lead name moves, "
            f"smaller-float sector peers are systematically scanned and positioned "
            f"as sympathy plays. Layer 8 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="sector_sympathy",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_sector_sympathy_decision] {type(_exc).__name__}: {_exc}")


def log_oi_build_decision(ticker: str, oi_pct: float, oi_chg: int,
                          strike: float, expiry: str, days_out: int, pts: float):
    """Log an OI accumulation build signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      oi_pct>=50% (pts=2.0) -> 0.85
      oi_pct>=25% (pts=1.5) -> 0.72
      oi_pct>=0%  (pts=1.0) -> 0.60
    """
    try:
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        reasoning = (
            f"OI accumulation build flagged {ticker}: open interest rose {oi_pct:.1f}% "
            f"({oi_chg:+,} contracts) on the ${strike:.2f} strike expiring {expiry} "
            f"({days_out}d out). "
            f"Rising OI on a single strike indicates NEW positions being opened — "
            f"not roll or hedge activity — consistent with directional conviction "
            f"building ahead of a move. Layer 1 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="oi_build",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_oi_build_decision] {type(_exc).__name__}: {_exc}")


def log_gamma_decision(ticker: str, fir: float, vol_oi: float,
                       score: float, price_change_pct: float,
                       top_strike):
    """Log a gamma pressure scan signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      fir>=5 (pts=2.0) -> 0.85
      fir>=3 (pts=1.5) -> 0.72
      fir>=1.2 (pts=1.0, SQL floor in _scan_one) -> 0.60
    """
    try:
        pts = 2.0 if fir >= 5 else 1.5 if fir >= 3 else 1.0
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        strike_str = f"${top_strike:.0f}" if top_strike else "near-ATM"
        direction  = "+" if price_change_pct >= 0 else ""
        reasoning = (
            f"Gamma pressure scan flagged {ticker}: Float Impact Ratio {fir:.2f}% "
            f"(dealer forced-share demand as % of float), Vol/OI {vol_oi:.1f}x "
            f"(fresh call buying relative to existing open interest), "
            f"concentrated near the {strike_str} strike. "
            f"Price {direction}{price_change_pct:.1f}% today — dealer delta-hedging of "
            f"fresh call volume is creating forced share demand. "
            f"Composite score: {score:.1f}. Layer 2 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="gamma_pressure_scan",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
        )
    except Exception as _exc:
        print(f"[log_gamma_decision] {type(_exc).__name__}: {_exc}")


def log_charm_decision(ticker: str, strike: float, expiry: str,
                       days_out: int, oi: int, otm_pct: float,
                       charm_score: float):
    """Log a charm cascade signal fire to agent_decisions.

    Confidence is mapped directly from the existing pts tier spec in main.py —
    no invented coefficient:
      charm_score>=1000 (pts=2.0) -> 0.85
      charm_score>=400  (pts=1.5) -> 0.72
      charm_score>=0    (pts=1.0) -> 0.60

    Previous formula min(0.88, 0.40 + min(charm_score,200)*0.002) was critically
    wrong: any charm_score>=200 gave confidence=0.80 regardless of tier, meaning
    pts=1.0 and pts=2.0 signals were indistinguishable above the 200 cap.
    """
    try:
        pts = 2.0 if charm_score >= 1000 else 1.5 if charm_score >= 400 else 1.0
        _CONFIDENCE_MAP = {2.0: 0.85, 1.5: 0.72, 1.0: 0.60}
        confidence = _CONFIDENCE_MAP.get(pts, 0.60)
        otm_label = f"{abs(otm_pct):.1f}% {'OTM' if otm_pct > 0 else 'ITM'}"
        reasoning = (
            f"Charm cascade flagged {ticker}: ${strike:.0f} strike expiring {expiry} "
            f"({days_out}d out, {otm_label}, {oi:,} open interest). "
            f"At ≤10 days to expiry in this strike zone, charm (dDelta/dTime) is near "
            f"its maximum — dealer hedge ratios rise automatically each calendar day "
            f"even without price movement, creating deterministic forced buying. "
            f"Charm score: {charm_score:.1f}. Layer 3 conviction tier: {pts:.1f} pts."
        )
        dl.log_decision(
            signal_name="charm_cascade",
            decision_type="trade",
            reasoning=reasoning,
            ticker=ticker,
            direction="long",
            confidence=confidence,
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
