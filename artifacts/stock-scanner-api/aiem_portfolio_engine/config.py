"""
aiem_portfolio_engine/config.py
Portfolio engine configuration — limits, flags, and config fingerprint.
Edit ONLY here; import everywhere else.
"""
import hashlib, json

PE_GATING_ENABLED = False

PORTFOLIO_CAPITAL        = 100_000.0
CONTRACT_MULTIPLIER      = 100

MAX_TICKER_CONCENTRATION      = 0.20
MAX_SECTOR_CONCENTRATION      = 0.35
MAX_INDUSTRY_CONCENTRATION    = 0.25
MAX_STRATEGY_FAMILY_CONC      = 0.40
MAX_EXPIRATION_CONCENTRATION  = 0.50
MAX_STRIKE_AREA_CONC          = 0.30
MAX_BULLISH_CONCENTRATION     = 0.65
MAX_BEARISH_CONCENTRATION     = 0.65
MAX_LONG_VOL_CONCENTRATION    = 0.50
MAX_SHORT_VOL_CONCENTRATION   = 0.40
MAX_UNDEFINED_RISK_EXPOSURE   = 0.00
MAX_SIMULTANEOUS_POSITIONS    = 10

MAX_BUYING_POWER_UTILIZATION   = 0.80
MAX_PORTFOLIO_RISK_UTILIZATION = 0.50

MAX_CORRELATION_CLUSTER_EXP    = 0.30
CORRELATION_LOOKBACK_DAYS      = 30
CORRELATION_HIGH_THRESHOLD     = 0.70
CORRELATION_EXTREME_THRESHOLD  = 0.85

MAX_PORTFOLIO_DELTA  = 300.0
MAX_PORTFOLIO_GAMMA  = 100.0
MAX_PORTFOLIO_VEGA   = 500.0
MAX_PORTFOLIO_THETA  = -200.0

DAILY_LOSS_LIMIT          = 2_000.0
STRESS_TEST_LOSS_LIMIT    = 15_000.0
LIQUIDITY_ADJ_LOSS_LIMIT  = 12_000.0

INDUSTRY_GROUPS: dict = {
    "cloud_infra":    {"AMZN", "MSFT", "GOOG", "GOOGL", "ORCL"},
    "consumer_chips": {"NVDA", "AMD", "INTC", "AVGO", "QCOM"},
    "ev_auto":        {"TSLA", "RIVN", "LCID", "F", "GM"},
    "social_media":   {"META", "SNAP", "PINS"},
    "streaming":      {"NFLX", "DIS", "WBD", "PARA"},
    "biotech_dev":    {"MRNA", "BNTX", "NVAX", "REGN"},
    "crypto_mining":  {"COIN", "MARA", "RIOT", "HIVE"},
}

GATE_STEPS = [
    "S01_reconcile_positions",
    "S02_greeks_before",
    "S03_concentration_before",
    "S04_correlation_risk",
    "S05_stress_before",
    "S06_liquidity_before",
    "S07_risk_budget_before",
    "S08_greeks_after",
    "S09_concentration_after",
    "S10_stress_after",
    "S11_liquidity_after",
    "S12_risk_budget_after",
    "S13_optimize_decide",
]

NOT_IMPLEMENTED_V1 = [
    "intraday_correlation: only EOD polygon_market_daily available; no intraday bar history",
    "market_depth_L2: no L2 order book feed; same architectural constraint as EI v1 partial_fill_probability",
    "candidate_combination_optimization: combinatorial explosion; single-candidate-vs-cash in v1",
    "common_factor_exposure: sector/beta/named-cluster only; no Fama-French factor model",
    "pending_orders: paper system has no pending-order state; field is always []",
    "realized_pnl_intraday: P&L computed at close event only, not tracked intraday",
    "tail_risk_correlation: no multi-asset tail-risk model; named clusters are the proxy",
    "macro_event_overlap: no FOMC/CPI calendar integrated; positions not screened for same event week",
    "earnings_overlap: no earnings date API; positions not screened for same earnings window",
]

_PE_CONFIG_KEYS = [
    "PE_GATING_ENABLED", "PORTFOLIO_CAPITAL", "CONTRACT_MULTIPLIER",
    "MAX_TICKER_CONCENTRATION", "MAX_SECTOR_CONCENTRATION", "MAX_INDUSTRY_CONCENTRATION",
    "MAX_STRATEGY_FAMILY_CONC", "MAX_EXPIRATION_CONCENTRATION",
    "MAX_BULLISH_CONCENTRATION", "MAX_BEARISH_CONCENTRATION",
    "MAX_LONG_VOL_CONCENTRATION", "MAX_SHORT_VOL_CONCENTRATION", "MAX_SIMULTANEOUS_POSITIONS",
    "MAX_BUYING_POWER_UTILIZATION", "MAX_PORTFOLIO_RISK_UTILIZATION",
    "MAX_CORRELATION_CLUSTER_EXP", "CORRELATION_LOOKBACK_DAYS",
    "MAX_PORTFOLIO_DELTA", "MAX_PORTFOLIO_GAMMA", "MAX_PORTFOLIO_VEGA", "MAX_PORTFOLIO_THETA",
    "DAILY_LOSS_LIMIT", "STRESS_TEST_LOSS_LIMIT", "LIQUIDITY_ADJ_LOSS_LIMIT",
    "MAX_INDUSTRY_CONCENTRATION", "MAX_STRIKE_AREA_CONC",
]


def pe_config_sha() -> str:
    _g = globals()
    blob = json.dumps({k: _g[k] for k in _PE_CONFIG_KEYS}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
