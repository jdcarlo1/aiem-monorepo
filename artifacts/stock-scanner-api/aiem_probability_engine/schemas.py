"""
schemas.py - output contracts for the AIEM Probability Engine, matching
the spec's per-ticker probability report format.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class HorizonProbability:
    horizon_days: int
    prob_up: float
    n_training_samples: int
    is_trustworthy: bool          # n_training_samples >= MIN_SAMPLES_FLOOR
    model_type: str
    calibration_bucket_n: Optional[int] = None


@dataclass
class ProbabilityReport:
    ticker: str
    signal_date: str
    horizons: Dict[int, HorizonProbability]   # keyed by horizon_days
    confidence: float                          # 0-1, lowered by model disagreement
    top_contributing_layers: List[str]
    regime_tag: Optional[str] = None
    # Deliberately NOT named with "bps": this is (prob_up - 0.5) * 100 minus
    # an estimated round-trip cost proxy - a probability-distance-from-
    # coinflip measure in percentage points, NOT a backtested/historical
    # return. "bps" conventionally implies a return figure and would be
    # misread by anyone skimming past the warnings list. See
    # context.edge_after_cost() for the exact computation.
    edge_after_cost_prob_pts: Optional[float] = None
    data_tier_used: str = "tier1_dominant"     # honesty flag, see config.py
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "signal_date": self.signal_date,
            "prob_up_1d": self.horizons[1].prob_up if 1 in self.horizons else None,
            "prob_up_2d": self.horizons[2].prob_up if 2 in self.horizons else None,
            "prob_up_3d": self.horizons[3].prob_up if 3 in self.horizons else None,
            "prob_up_4d": self.horizons[4].prob_up if 4 in self.horizons else None,
            "confidence": self.confidence,
            "top_contributing_layers": self.top_contributing_layers,
            "regime_tag": self.regime_tag,
            "edge_after_cost_prob_pts": self.edge_after_cost_prob_pts,
            "data_tier_used": self.data_tier_used,
            "warnings": self.warnings,
            "_horizon_detail": {
                h: {
                    "prob_up": hp.prob_up,
                    "n_training_samples": hp.n_training_samples,
                    "is_trustworthy": hp.is_trustworthy,
                    "model_type": hp.model_type,
                }
                for h, hp in self.horizons.items()
            },
        }
