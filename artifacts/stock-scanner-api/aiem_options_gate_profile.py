"""
Options Engine gate profile — tunable hard/soft thresholds.

Why this exists
---------------
2026-08-06 production evidence: daily OE processed 15 candidates and took
0 trades. Blockers were NOT risk/reward hard rejects:

  * 10× NO_TRADE_GATES / NO_LIQUID_CONTRACTS (bid=0/ask=0 / no liquid legs)
  * 5× FAILED (missing Polygon/OSS market data)

D5_risk_reward is a REQ6 *scoring weight* (10%), not a hard gate. The walls
that kill early-morning movers are:

  1. Liquid-chain predicate requiring BOTH bid>0 and ask>0
  2. verify_options_decision_inputs requiring BOTH call + put field sets
  3. Strict OI / volume / spread / PoP floors once Tradier fills them
  4. Soft decision score≥55 and margin≥10

Profiles (OE_GATE_PROFILE env, default: balanced)
-------------------------------------------------
  strict       — historical thresholds (pre-2026-08-06)
  balanced     — moderate relaxation for morning opportunity capture (DEFAULT)
  opportunity  — aggressive; more paper fills, wider spreads / thinner OI

Override any single knob with OE_GATE_* env vars (see resolve_gate_profile).
"""
from __future__ import annotations

import os
from typing import Any


_PROFILES: dict[str, dict[str, Any]] = {
    "strict": {
        "min_oi": 500,
        "min_volume": 100,
        "max_spread_pct": 0.20,
        "max_slippage_pct": 0.15,
        "min_delta": 0.20,
        "min_pop": 0.35,
        "min_dte": 5,
        "score_min": 55.0,
        "margin_min": 10.0,
        "allow_one_sided_quotes": False,
        "allow_single_leg": False,
        "one_sided_bid_frac": 0.85,
    },
    "balanced": {
        "min_oi": 250,
        "min_volume": 50,
        "max_spread_pct": 0.28,
        "max_slippage_pct": 0.20,
        "min_delta": 0.18,
        "min_pop": 0.30,
        "min_dte": 5,
        "score_min": 50.0,
        "margin_min": 8.0,
        "allow_one_sided_quotes": True,
        "allow_single_leg": True,
        "one_sided_bid_frac": 0.85,
    },
    "opportunity": {
        "min_oi": 100,
        "min_volume": 25,
        "max_spread_pct": 0.35,
        "max_slippage_pct": 0.25,
        "min_delta": 0.15,
        "min_pop": 0.25,
        "min_dte": 3,
        "score_min": 45.0,
        "margin_min": 5.0,
        "allow_one_sided_quotes": True,
        "allow_single_leg": True,
        "one_sided_bid_frac": 0.80,
    },
}


def _env_name(key: str) -> str:
    return f"OE_GATE_{key.upper()}"


def _coerce(raw: str, proto: Any) -> Any:
    if isinstance(proto, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(proto, int) and not isinstance(proto, bool):
        return int(float(raw))
    if isinstance(proto, float):
        return float(raw)
    return raw


def resolve_gate_profile(profile_name: str | None = None) -> dict[str, Any]:
    """Return merged gate knobs for the active profile (+ per-key env overrides)."""
    name = (profile_name or os.environ.get("OE_GATE_PROFILE") or "balanced").strip().lower()
    if name not in _PROFILES:
        name = "balanced"
    cfg = dict(_PROFILES[name])
    cfg["profile"] = name

    for key, proto in list(_PROFILES["balanced"].items()):
        env_key = _env_name(key)
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        try:
            cfg[key] = _coerce(raw, proto)
        except Exception:
            pass
    return cfg


def describe_gate_profile(cfg: dict[str, Any] | None = None) -> str:
    c = cfg or resolve_gate_profile()
    return (
        f"profile={c.get('profile')} "
        f"oi>={c['min_oi']} vol>={c['min_volume']} "
        f"spread<={c['max_spread_pct']} slip<={c['max_slippage_pct']} "
        f"|δ|>={c['min_delta']} pop>={c['min_pop']} dte>={c['min_dte']} "
        f"score>={c['score_min']} margin>={c['margin_min']} "
        f"one_sided={c['allow_one_sided_quotes']} "
        f"single_leg={c['allow_single_leg']}"
    )
