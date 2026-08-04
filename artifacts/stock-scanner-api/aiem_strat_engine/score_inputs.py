"""
score_inputs.py — Phase 5: Score Input Assembly

Assembles the 12 mandatory real score inputs defined in the Options Autonomy
Directive §8.  Every field must be a real computed/fetched value.
None is the explicit sentinel for "module unavailable / data not fetched" —
it is NEVER a hidden 0.5 or other numeric default.

The 12 inputs:
  1. pattern_score          — from aiem_pattern_engine
  2. pm_intel_score         — from premarket intelligence module
  3. mtf_alignment_score    — from multi-timeframe alignment module
  4. iv_rank                — [0,100] from Tradier/chain history
  5. iv_percentile          — [0,100] from Tradier/chain history
  6. market_regime          — from polygon_rvol_scan or macro DB
  7. volatility_regime      — "HIGH_IV"|"LOW_IV" from atm_iv threshold
  8. liquidity_score        — per-strategy from liq_sc(legs) or chain quality
  9. signal_quality         — composite signal confidence [0,1]
 10. direction_confidence   — directional conviction [0,1]
 11. expected_slippage      — dollars from slippage_estimate(legs, atm_iv)
 12. fill_probability       — fill probability from chain quality or liq proxy

Source provenance tags:
  "live"       — fetched/computed fresh this run
  "db"         — read from database (may be hours old)
  "derived"    — computed from another live value
  "unavailable"— module not called or data absent; value is None
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any


@dataclass
class ScoreInputs:
    """
    All 12 real score inputs per §8.
    Each field is either a real value or explicitly None (never a silent default).
    source_map records the provenance of each input for audit.
    """
    # ── The 12 required inputs ────────────────────────────────────────────────
    pattern_score:          Optional[float] = None   # [0,1]; None = pattern engine unavailable
    pm_intel_score:         Optional[float] = None   # [0,1]; None = module not called
    mtf_alignment_score:    Optional[float] = None   # [0,1]; None = module not called
    iv_rank:                Optional[float] = None   # [0,100]; None = not fetched
    iv_percentile:          Optional[float] = None   # [0,100]; None = not fetched
    market_regime:          str             = "NEUTRAL"  # always has a value
    volatility_regime:      str             = "UNKNOWN"  # always set from atm_iv
    liquidity_score:        Optional[float] = None   # [0,1]; None = no chain data
    signal_quality:         Optional[float] = None   # [0,1]; None = pattern unavailable
    direction_confidence:   Optional[float] = None   # [0,1]; None = not derived
    expected_slippage:      Optional[float] = None   # dollars; None = not computed
    fill_probability:       Optional[float] = None   # [0,1]; None = not estimated

    # ── Audit metadata ────────────────────────────────────────────────────────
    source_map:             Dict[str, str]  = field(default_factory=dict)
    fetched_at_utc:         str             = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate_no_hidden_defaults(self) -> list[str]:
        """
        Check that no numeric field is exactly 0.5 while its source is 'live'.
        A 0.5 from a live computation is fine; 0.5 as a fallback is a hidden default.
        Returns list of violation messages.
        """
        warnings = []
        numeric_fields = [
            "pattern_score", "pm_intel_score", "mtf_alignment_score",
            "liquidity_score", "signal_quality", "direction_confidence",
            "fill_probability",
        ]
        for fname in numeric_fields:
            val = getattr(self, fname)
            src = self.source_map.get(fname, "unknown")
            if val is not None and val == 0.5 and src not in ("live", "derived", "db"):
                warnings.append(
                    f"HIDDEN_DEFAULT_RISK: {fname}=0.5 but source={src!r} — "
                    "check whether this is a genuine computed value"
                )
        return warnings


def build_score_inputs(
    *,
    # ── From pattern engine ───────────────────────────────────────────────────
    pattern_score:        Optional[float]  = None,
    pattern_source:       str              = "unavailable",
    signal_quality:       Optional[float]  = None,
    signal_quality_source: str             = "unavailable",
    # ── From premarket + MTF modules ─────────────────────────────────────────
    pm_intel_score:       Optional[float]  = None,
    pm_intel_source:      str              = "unavailable",
    mtf_alignment_score:  Optional[float]  = None,
    mtf_alignment_source: str              = "unavailable",
    # ── From IV / options chain ───────────────────────────────────────────────
    iv_rank:              Optional[float]  = None,
    iv_rank_source:       str              = "unavailable",
    iv_percentile:        Optional[float]  = None,
    iv_percentile_source: str              = "unavailable",
    # ── Regime (always populated) ─────────────────────────────────────────────
    market_regime:        str              = "NEUTRAL",
    market_regime_source: str              = "db",
    volatility_regime:    str              = "UNKNOWN",
    vol_regime_source:    str              = "derived",
    # ── Liquidity / execution quality ─────────────────────────────────────────
    liquidity_score:      Optional[float]  = None,
    liquidity_source:     str              = "unavailable",
    direction_confidence: Optional[float]  = None,
    dir_confidence_source: str             = "unavailable",
    expected_slippage:    Optional[float]  = None,
    slippage_source:      str              = "unavailable",
    fill_probability:     Optional[float]  = None,
    fill_prob_source:     str              = "unavailable",
) -> ScoreInputs:
    """
    Construct a ScoreInputs with explicit provenance tags for every field.
    Callers supply only values they have actually computed/fetched — everything
    else stays None with source="unavailable".
    """
    source_map = {
        "pattern_score":        pattern_source,
        "pm_intel_score":       pm_intel_source,
        "mtf_alignment_score":  mtf_alignment_source,
        "iv_rank":              iv_rank_source,
        "iv_percentile":        iv_percentile_source,
        "market_regime":        market_regime_source,
        "volatility_regime":    vol_regime_source,
        "liquidity_score":      liquidity_source,
        "signal_quality":       signal_quality_source,
        "direction_confidence": dir_confidence_source,
        "expected_slippage":    slippage_source,
        "fill_probability":     fill_prob_source,
    }

    inputs = ScoreInputs(
        pattern_score=pattern_score,
        pm_intel_score=pm_intel_score,
        mtf_alignment_score=mtf_alignment_score,
        iv_rank=iv_rank,
        iv_percentile=iv_percentile,
        market_regime=market_regime,
        volatility_regime=volatility_regime,
        liquidity_score=liquidity_score,
        signal_quality=signal_quality,
        direction_confidence=direction_confidence,
        expected_slippage=expected_slippage,
        fill_probability=fill_probability,
        source_map=source_map,
    )

    warnings = inputs.validate_no_hidden_defaults()
    if warnings:
        import logging
        for w in warnings:
            logging.getLogger("aiem_strat_engine.score_inputs").warning(w)

    return inputs
