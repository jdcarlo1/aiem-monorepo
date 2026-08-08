from aiem_broker.tradier_config import TRADIER_API_BASE as _TAB
"""
config.py — All tuneable thresholds, weights, and constants for the
Advanced Strategy Engine. Edit only here; import everywhere else.
"""
import hashlib, json, os

# ── Tradier data source ─────────────────────────────────────────────────────
TRADIER_TOKEN   = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN", "")
TRADIER_BASE    = f"{_TAB}/v1/markets"
CHAIN_CACHE_TTL = 300      # seconds

# ── Commission / fee model ──────────────────────────────────────────────────
COMMISSION_PER_LEG   = 0.65   # $ per contract (one leg, one direction)
COMMISSION_BASE_TRADE = 0.00  # platform base (Tradier brokerage = $0)
REG_FEE_PER_CONTRACT  = 0.02  # regulatory fee estimate
OCC_FER_CLEARING_FEE  = 0.01
DEFAULT_SLIPPAGE_FRAC = 0.005  # 0.5% of mid as default slippage guess

# ── Eligibility hard gates (Phase 4 §6 — hard reject any leg that fails) ────
MIN_DTE             = 2       # days to expiry
MAX_BID_ASK_WIDTH   = 0.20    # fraction of mid — hard reject when spread > 20% of mid
MIN_OPEN_INTEREST   = 50      # per leg — hard reject OI < 50
MIN_VOLUME          = 20      # per leg (day's volume) — hard reject vol < 20
MIN_IV              = 0.05    # 5% IV floor (below = unreliable pricing)
MAX_IV              = 4.00    # 400% IV ceiling (meme/event noise)
MIN_PoP             = 0.25    # 25% PoP floor for autonomous trades
MIN_EV_AFTER_COSTS  = -0.01   # EV must be >= -$0.01/dollar at risk
MAX_SPREAD_PER_FILL = 0.35    # max acceptable multi-leg fill spread vs mid
QUOTE_STALE_SECONDS = 300     # hard reject quotes older than this (seconds)

# ── Preferred thresholds (scoring only — not hard reject) ───────────────────
PREFER_MIN_OI         = 500   # preferred per-leg OI for full liquidity score
PREFER_MIN_VOLUME     = 100   # preferred per-leg day volume for full liquidity score
PREFER_MAX_SPREAD_PCT = 0.10  # spread ≤ 10% → full spread component in liquidity score

# ── Polygon options chain fallback ──────────────────────────────────────────
# Enable only after live-verified freshness and source logging are confirmed.
POLYGON_CHAIN_FALLBACK_ENABLED = False

# ── Strategy evaluation caps ───────────────────────────────────────────────
MAX_EVALUATIONS_PER_RUN   = 200   # hard cap to prevent combinatorial explosion
MAX_CAPITAL_PER_TRADE     = 5000  # $ max buying power per paper trade
MAX_CAPITAL_AT_RISK_PCT   = 0.05  # 5% portfolio at risk per trade
PORTFOLIO_CAPITAL         = 100_000  # paper portfolio size

# ── Capital Compounding Score weights ───────────────────────────────────────
# Total must sum to 1.0.
# pm_intel_score and mtf_alignment_score were added; thesis_fit/regime_fit/
# vol_regime_fit/pattern_confirmation/diversification_value reduced to compensate.
SCORE_WEIGHTS = {
    "pop":                    0.18,
    "ev_after_costs":         0.18,
    "capital_preservation":   0.14,
    "defined_risk_quality":   0.10,
    "capital_efficiency":     0.10,
    "liquidity":              0.10,
    "pm_intel_score":         0.04,   # premarket intelligence signal [0,1]
    "mtf_alignment_score":    0.04,   # multi-timeframe alignment [0,1]
    "thesis_fit":             0.03,   # reduced from 0.05
    "regime_fit":             0.03,   # reduced from 0.05
    "vol_regime_fit":         0.02,   # reduced from 0.03
    "pattern_confirmation":   0.03,   # reduced from 0.05; 0=contra, 0.5=neutral, 1=confirming
    "diversification_value":  0.01,   # reduced from 0.02
    # Sanity: 0.18+0.18+0.14+0.10+0.10+0.10+0.04+0.04+0.03+0.03+0.02+0.03+0.01 = 1.00
}
# Penalty multipliers (applied additively as negative score components)
SCORE_PENALTIES = {
    "max_loss_pct":     0.10,  # penalty per 10% of capital at risk
    "drawdown_risk":    0.05,
    "tail_risk":        0.08,
    "assignment_risk":  0.05,
    "event_risk":       0.05,
    "slippage_cost":    0.03,
    "complexity":       0.02,  # per extra leg beyond 2
    "concentration":    0.05,
}

# ── NO_TRADE baseline score ─────────────────────────────────────────────────
NO_TRADE_SCORE = 0.35   # strategies must beat this to be selected

# ── Execution Intelligence (EI) thresholds ──────────────────────────────────
# EI_GATING_ENABLED: set to True ONLY after execution_intelligence_verify.py
#   passes all checks against live data.  In observe mode (False) EI assesses
#   and logs but does NOT gate the pipeline.
EI_GATING_ENABLED          = False
EI_MIN_FILL_PROB           = 0.30   # minimum fill probability to allow trade
EI_MIN_LIQUIDITY_SCORE     = 0.25   # minimum EI liquidity score
EI_MIN_NET_EDGE            = -0.50  # net edge floor in dollars (lenient — catches only negative EV after all costs)
EI_MAX_SPREAD_PCT          = 0.35   # maximum bid/ask spread as fraction of mid (35%)
EI_MAX_TRANSACTION_COST_FRAC = 0.30 # transaction costs cannot exceed 30% of gross edge

# ── DTE buckets for template instantiation ──────────────────────────────────
DTE_SLOTS = {
    "ZERO_DTE": (0, 1),
    "WEEKLY":   (2, 8),
    "BIWEEKLY": (9, 17),
    "MONTHLY":  (18, 47),
    "BIMONTHLY":(48, 90),
    "QUARTERLY":(91, 180),
    "LEAPS":    (181, 730),
}

# ── Delta anchors for strategy instantiation ────────────────────────────────
DELTA_ANCHORS = {
    "DEEP_ITM":  0.80,
    "ITM":       0.65,
    "ATM":       0.50,
    "OTM_LIGHT": 0.35,
    "OTM":       0.25,
    "DEEP_OTM":  0.10,
}

# ── Config fingerprint (for audit) ─────────────────────────────────────────
def config_sha256() -> str:
    blob = json.dumps(
        {k: v for k, v in globals().items()
         if isinstance(v, (int, float, str, dict, list)) and not k.startswith("_")},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()
