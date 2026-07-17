"""
aiem_pattern_registry.py — Canonical pattern registry.

Maintains a DB-backed registry of every pattern detector with:
  - source_file, function_name, sha256 of function source
  - enabled/disabled flag
  - status: UNTESTED | PASS | FAIL
  - precision, recall, false_positive_rate, backtest_n

DB table: aiem_pattern_registry (auto-created on first use)

Rules:
  - Only PASS-status patterns contribute to the CCS pattern_score
  - UNTESTED patterns run and are logged but do not affect CCS
  - FAIL patterns are disabled and skipped entirely
  - SHA-256 is recomputed at registry build time; mismatch = re-test required
"""
from __future__ import annotations
import hashlib
import inspect
import os
import json
from typing import List, Dict, Any, Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS aiem_pattern_registry (
    id                 SERIAL PRIMARY KEY,
    pattern_name       VARCHAR(120) UNIQUE NOT NULL,
    category           VARCHAR(60)  NOT NULL,
    direction          VARCHAR(20),
    source_file        VARCHAR(300),
    function_name      VARCHAR(300),
    function_sha256    VARCHAR(64),
    enabled            BOOLEAN      DEFAULT TRUE,
    status             VARCHAR(20)  DEFAULT 'UNTESTED',
    precision_score    NUMERIC(6,4),
    recall_score       NUMERIC(6,4),
    false_positive_rate NUMERIC(6,4),
    false_negative_rate NUMERIC(6,4),
    backtest_n         INTEGER,
    last_tested        TIMESTAMP,
    notes              TEXT,
    created_at         TIMESTAMP    DEFAULT NOW(),
    updated_at         TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_apr_category ON aiem_pattern_registry(category);
CREATE INDEX IF NOT EXISTS idx_apr_enabled  ON aiem_pattern_registry(enabled, status);
"""


def _sha256_fn(fn) -> str:
    try:
        src = inspect.getsource(fn)
        return hashlib.sha256(src.encode()).hexdigest()
    except Exception:
        return "unavailable"


def _conn():
    return psycopg2.connect(_DB_URL)


def ensure_table():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(_CREATE_TABLE)
        conn.commit()


# ── Pattern catalog ───────────────────────────────────────────────────────────
# Each entry: (pattern_name, category, direction, source_file, function_name_str)
# function_name_str is used to look up the callable for SHA-256 computation.

def _get_catalog() -> List[Dict]:
    from candlestick_patterns import (
        is_doji, is_dragonfly_doji, is_gravestone_doji, is_long_legged_doji,
        is_hammer, is_inverted_hammer, is_hanging_man, is_shooting_star,
        is_bullish_marubozu, is_bearish_marubozu, is_spinning_top, is_high_wave,
        is_bullish_belt_hold, is_bearish_belt_hold,
        is_bullish_engulfing, is_bearish_engulfing, is_piercing_line,
        is_dark_cloud_cover, is_bullish_harami, is_bearish_harami,
        is_bullish_harami_cross, is_bearish_harami_cross,
        is_tweezer_tops, is_tweezer_bottoms, is_bullish_kicker, is_bearish_kicker,
        is_on_neck, is_in_neck, is_matching_low,
        is_morning_star, is_evening_star, is_morning_doji_star, is_evening_doji_star,
        is_three_white_soldiers, is_three_black_crows,
        is_three_inside_up, is_three_inside_down,
        is_three_outside_up, is_three_outside_down,
        is_abandoned_baby_bullish, is_abandoned_baby_bearish,
        is_three_stars_south, is_advance_block, is_deliberation,
        is_rising_three_methods, is_falling_three_methods,
        is_upside_gap_three_methods, is_downside_gap_three_methods,
        is_stick_sandwich, is_concealing_baby_swallow,
    )
    from aiem_harmonic_patterns import (
        _check_gartley, _check_bat, _check_butterfly, _check_crab,
        _check_deep_crab, _check_shark, _check_cypher, _check_abcd,
        _check_three_drives,
    )
    from aiem_wyckoff_vpa import (
        detect_volume_climax, detect_shakeout, detect_no_demand, detect_no_supply,
        detect_stopping_volume, detect_effort_vs_result, detect_volume_dryup,
        detect_selling_climax, detect_buying_climax,
        detect_spring, detect_upthrust, detect_sign_of_strength, detect_sign_of_weakness,
        detect_accumulation_phase, detect_distribution_phase,
    )
    from aiem_elliott_wave import (
        _validate_impulse, _validate_abc, _validate_zigzag, _validate_flat,
        _validate_triangle, _validate_double_three, _validate_triple_three,
    )

    CS = "candlestick_patterns.py"
    HR = "aiem_harmonic_patterns.py"
    WV = "aiem_wyckoff_vpa.py"
    EW = "aiem_elliott_wave.py"
    PS = "price_structure_patterns.py"

    return [
        # ── Candlestick: single-candle ───────────────────────────────────
        {"pattern_name": "doji",                  "category": "CANDLESTICK", "direction": "NEUTRAL",  "source_file": CS, "fn": is_doji},
        {"pattern_name": "dragonfly_doji",        "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_dragonfly_doji},
        {"pattern_name": "gravestone_doji",       "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_gravestone_doji},
        {"pattern_name": "long_legged_doji",      "category": "CANDLESTICK", "direction": "NEUTRAL",  "source_file": CS, "fn": is_long_legged_doji},
        {"pattern_name": "hammer",                "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_hammer},
        {"pattern_name": "inverted_hammer",       "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_inverted_hammer},
        {"pattern_name": "hanging_man",           "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_hanging_man},
        {"pattern_name": "shooting_star",         "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_shooting_star},
        {"pattern_name": "bullish_marubozu",      "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_bullish_marubozu},
        {"pattern_name": "bearish_marubozu",      "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_bearish_marubozu},
        {"pattern_name": "spinning_top",          "category": "CANDLESTICK", "direction": "NEUTRAL",  "source_file": CS, "fn": is_spinning_top},
        {"pattern_name": "high_wave",             "category": "CANDLESTICK", "direction": "NEUTRAL",  "source_file": CS, "fn": is_high_wave},
        {"pattern_name": "bullish_belt_hold",     "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_bullish_belt_hold},
        {"pattern_name": "bearish_belt_hold",     "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_bearish_belt_hold},
        # ── Candlestick: two-candle ──────────────────────────────────────
        {"pattern_name": "bullish_engulfing",     "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_bullish_engulfing},
        {"pattern_name": "bearish_engulfing",     "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_bearish_engulfing},
        {"pattern_name": "piercing_line",         "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_piercing_line},
        {"pattern_name": "dark_cloud_cover",      "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_dark_cloud_cover},
        {"pattern_name": "bullish_harami",        "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_bullish_harami},
        {"pattern_name": "bearish_harami",        "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_bearish_harami},
        {"pattern_name": "bullish_harami_cross",  "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_bullish_harami_cross},
        {"pattern_name": "bearish_harami_cross",  "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_bearish_harami_cross},
        {"pattern_name": "tweezer_tops",          "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_tweezer_tops},
        {"pattern_name": "tweezer_bottoms",       "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_tweezer_bottoms},
        {"pattern_name": "bullish_kicker",        "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_bullish_kicker},
        {"pattern_name": "bearish_kicker",        "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_bearish_kicker},
        {"pattern_name": "on_neck",               "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_on_neck},
        {"pattern_name": "in_neck",               "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_in_neck},
        {"pattern_name": "matching_low",          "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_matching_low},
        # ── Candlestick: three-candle ────────────────────────────────────
        {"pattern_name": "morning_star",          "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_morning_star},
        {"pattern_name": "evening_star",          "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_evening_star},
        {"pattern_name": "morning_doji_star",     "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_morning_doji_star},
        {"pattern_name": "evening_doji_star",     "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_evening_doji_star},
        {"pattern_name": "three_white_soldiers",  "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_three_white_soldiers},
        {"pattern_name": "three_black_crows",     "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_three_black_crows},
        {"pattern_name": "three_inside_up",       "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_three_inside_up},
        {"pattern_name": "three_inside_down",     "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_three_inside_down},
        {"pattern_name": "three_outside_up",      "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_three_outside_up},
        {"pattern_name": "three_outside_down",    "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_three_outside_down},
        {"pattern_name": "abandoned_baby_bullish","category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_abandoned_baby_bullish},
        {"pattern_name": "abandoned_baby_bearish","category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_abandoned_baby_bearish},
        {"pattern_name": "three_stars_south",     "category": "CANDLESTICK", "direction": "BULLISH",  "source_file": CS, "fn": is_three_stars_south},
        {"pattern_name": "advance_block",         "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_advance_block},
        {"pattern_name": "deliberation",          "category": "CANDLESTICK", "direction": "BEARISH",  "source_file": CS, "fn": is_deliberation},
        # ── Candlestick: multi-candle ────────────────────────────────────
        {"pattern_name": "rising_three_methods",       "category": "CANDLESTICK", "direction": "BULLISH", "source_file": CS, "fn": is_rising_three_methods},
        {"pattern_name": "falling_three_methods",      "category": "CANDLESTICK", "direction": "BEARISH", "source_file": CS, "fn": is_falling_three_methods},
        {"pattern_name": "upside_gap_three_methods",   "category": "CANDLESTICK", "direction": "BULLISH", "source_file": CS, "fn": is_upside_gap_three_methods},
        {"pattern_name": "downside_gap_three_methods", "category": "CANDLESTICK", "direction": "BEARISH", "source_file": CS, "fn": is_downside_gap_three_methods},
        {"pattern_name": "stick_sandwich",             "category": "CANDLESTICK", "direction": "BULLISH", "source_file": CS, "fn": is_stick_sandwich},
        {"pattern_name": "concealing_baby_swallow",    "category": "CANDLESTICK", "direction": "BULLISH", "source_file": CS, "fn": is_concealing_baby_swallow},
        # ── Chart structure ──────────────────────────────────────────────
        {"pattern_name": "double_top",             "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "double_bottom",          "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "triple_top",             "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "triple_bottom",          "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "head_and_shoulders",     "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "inverse_head_and_shoulders", "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "complex_head_and_shoulders", "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "diamond_top",            "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "diamond_bottom",         "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "triangle_ascending",     "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "triangle_descending",    "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "triangle_symmetrical",   "category": "CHART_STRUCTURE", "direction": "NEUTRAL", "source_file": PS, "fn": None},
        {"pattern_name": "wedge_rising",           "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "wedge_falling",          "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "channel_ascending",      "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "channel_descending",     "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "channel_horizontal",     "category": "CHART_STRUCTURE", "direction": "NEUTRAL", "source_file": PS, "fn": None},
        {"pattern_name": "broadening_symmetrical", "category": "CHART_STRUCTURE", "direction": "NEUTRAL", "source_file": PS, "fn": None},
        {"pattern_name": "broadening_top",         "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "broadening_bottom",      "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "flag_or_pennant",        "category": "CHART_STRUCTURE", "direction": "BOTH",    "source_file": PS, "fn": None},
        {"pattern_name": "cup_and_handle",         "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "inverted_cup_and_handle","category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "rounded_bottom",         "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "rounded_top",            "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "breakaway_gap_up",       "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "breakaway_gap_down",     "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "runaway_gap_up",         "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "runaway_gap_down",       "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "exhaustion_gap_up",      "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "exhaustion_gap_down",    "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "island_reversal_bullish","category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "island_reversal_bearish","category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        {"pattern_name": "measured_move_up",       "category": "CHART_STRUCTURE", "direction": "BULLISH", "source_file": PS, "fn": None},
        {"pattern_name": "measured_move_down",     "category": "CHART_STRUCTURE", "direction": "BEARISH", "source_file": PS, "fn": None},
        # ── Harmonic ────────────────────────────────────────────────────
        {"pattern_name": "harmonic_gartley",       "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_gartley},
        {"pattern_name": "harmonic_bat",           "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_bat},
        {"pattern_name": "harmonic_butterfly",     "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_butterfly},
        {"pattern_name": "harmonic_crab",          "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_crab},
        {"pattern_name": "harmonic_deep_crab",     "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_deep_crab},
        {"pattern_name": "harmonic_shark",         "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_shark},
        {"pattern_name": "harmonic_cypher",        "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_cypher},
        {"pattern_name": "harmonic_abcd",          "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_abcd},
        {"pattern_name": "harmonic_three_drives",  "category": "HARMONIC", "direction": "BOTH", "source_file": HR, "fn": _check_three_drives},
        # ── VPA ─────────────────────────────────────────────────────────
        {"pattern_name": "vpa_volume_climax",     "category": "VPA", "direction": "BOTH",    "source_file": WV, "fn": detect_volume_climax},
        {"pattern_name": "vpa_shakeout",          "category": "VPA", "direction": "BULLISH", "source_file": WV, "fn": detect_shakeout},
        {"pattern_name": "vpa_no_demand",         "category": "VPA", "direction": "BEARISH", "source_file": WV, "fn": detect_no_demand},
        {"pattern_name": "vpa_no_supply",         "category": "VPA", "direction": "BULLISH", "source_file": WV, "fn": detect_no_supply},
        {"pattern_name": "vpa_stopping_volume",   "category": "VPA", "direction": "BULLISH", "source_file": WV, "fn": detect_stopping_volume},
        {"pattern_name": "vpa_effort_vs_result",  "category": "VPA", "direction": "BOTH",    "source_file": WV, "fn": detect_effort_vs_result},
        {"pattern_name": "vpa_volume_dryup",      "category": "VPA", "direction": "BOTH",    "source_file": WV, "fn": detect_volume_dryup},
        # ── Wyckoff ─────────────────────────────────────────────────────
        {"pattern_name": "wyckoff_selling_climax","category": "WYCKOFF", "direction": "BULLISH", "source_file": WV, "fn": detect_selling_climax},
        {"pattern_name": "wyckoff_buying_climax", "category": "WYCKOFF", "direction": "BEARISH", "source_file": WV, "fn": detect_buying_climax},
        {"pattern_name": "wyckoff_spring",        "category": "WYCKOFF", "direction": "BULLISH", "source_file": WV, "fn": detect_spring},
        {"pattern_name": "wyckoff_upthrust",      "category": "WYCKOFF", "direction": "BEARISH", "source_file": WV, "fn": detect_upthrust},
        {"pattern_name": "wyckoff_sos",           "category": "WYCKOFF", "direction": "BULLISH", "source_file": WV, "fn": detect_sign_of_strength},
        {"pattern_name": "wyckoff_sow",           "category": "WYCKOFF", "direction": "BEARISH", "source_file": WV, "fn": detect_sign_of_weakness},
        {"pattern_name": "wyckoff_accumulation",  "category": "WYCKOFF", "direction": "BULLISH", "source_file": WV, "fn": detect_accumulation_phase},
        {"pattern_name": "wyckoff_distribution",  "category": "WYCKOFF", "direction": "BEARISH", "source_file": WV, "fn": detect_distribution_phase},
        # ── Elliott Wave ─────────────────────────────────────────────────
        {"pattern_name": "elliott_impulse",       "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_impulse},
        {"pattern_name": "elliott_abc",           "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_abc},
        {"pattern_name": "elliott_zigzag",        "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_zigzag},
        {"pattern_name": "elliott_flat",          "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_flat},
        {"pattern_name": "elliott_triangle",      "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_triangle},
        {"pattern_name": "elliott_double_three",  "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_double_three},
        {"pattern_name": "elliott_triple_three",  "category": "ELLIOTT_WAVE", "direction": "BOTH", "source_file": EW, "fn": _validate_triple_three},
    ]


def build_registry() -> int:
    """
    Sync catalog to DB: INSERT new patterns, UPDATE sha256 for changed ones.
    Returns count of upserted rows.
    """
    ensure_table()
    catalog = _get_catalog()
    upserted = 0
    with _conn() as conn, conn.cursor() as cur:
        for entry in catalog:
            sha = _sha256_fn(entry["fn"]) if entry.get("fn") else "classify_chart_patterns"
            cur.execute("""
                INSERT INTO aiem_pattern_registry
                    (pattern_name, category, direction, source_file, function_name, function_sha256,
                     enabled, status)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, 'UNTESTED')
                ON CONFLICT (pattern_name) DO UPDATE SET
                    function_sha256 = EXCLUDED.function_sha256,
                    source_file     = EXCLUDED.source_file,
                    updated_at      = NOW()
            """, (
                entry["pattern_name"], entry["category"],
                entry.get("direction", "BOTH"), entry["source_file"],
                entry["fn"].__name__ if entry.get("fn") else "classify_chart_patterns",
                sha,
            ))
            upserted += cur.rowcount
        conn.commit()
    return upserted


def get_registry(enabled_only: bool = False) -> List[Dict]:
    """Fetch all (or enabled-only) registry rows as dicts."""
    ensure_table()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if enabled_only:
            cur.execute("SELECT * FROM aiem_pattern_registry WHERE enabled=TRUE ORDER BY category, pattern_name")
        else:
            cur.execute("SELECT * FROM aiem_pattern_registry ORDER BY category, pattern_name")
        return [dict(r) for r in cur.fetchall()]


def get_pass_patterns() -> List[str]:
    """Return pattern names that are ENABLED and status=PASS — contribute to CCS."""
    ensure_table()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT pattern_name FROM aiem_pattern_registry
            WHERE enabled=TRUE AND status='PASS'
        """)
        return [r[0] for r in cur.fetchall()]


def update_pattern_test_result(
    pattern_name: str,
    status: str,
    precision_score: Optional[float] = None,
    recall_score: Optional[float] = None,
    false_positive_rate: Optional[float] = None,
    false_negative_rate: Optional[float] = None,
    backtest_n: Optional[int] = None,
    notes: str = None,
):
    """Update test results for a specific pattern."""
    import datetime
    ensure_table()
    enabled = status != "FAIL"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE aiem_pattern_registry SET
                status = %s,
                enabled = %s,
                precision_score = %s,
                recall_score = %s,
                false_positive_rate = %s,
                false_negative_rate = %s,
                backtest_n = %s,
                notes = %s,
                last_tested = %s,
                updated_at = NOW()
            WHERE pattern_name = %s
        """, (
            status, enabled, precision_score, recall_score,
            false_positive_rate, false_negative_rate,
            backtest_n, notes,
            datetime.datetime.utcnow(),
            pattern_name,
        ))
        conn.commit()


def print_registry_summary():
    """Print a human-readable registry summary to stdout."""
    rows = get_registry()
    by_status = {}
    for r in rows:
        s = r["status"]
        by_status.setdefault(s, []).append(r)
    print(f"\n{'='*80}")
    print(f"AIEM PATTERN REGISTRY — {len(rows)} total patterns")
    print(f"{'='*80}")
    for status in ["PASS", "FAIL", "UNTESTED"]:
        grp = by_status.get(status, [])
        print(f"\n  [{status}] — {len(grp)} patterns")
        for r in sorted(grp, key=lambda x: x["category"]):
            prec = f"P={r['precision_score']:.2f}" if r.get("precision_score") else "P=?"
            rec  = f"R={r['recall_score']:.2f}"    if r.get("recall_score")    else "R=?"
            enab = "ENABLED" if r["enabled"] else "DISABLED"
            sha  = (r.get("function_sha256") or "")[:10]
            print(f"    {r['pattern_name']:<45} {r['category']:<20} {r['direction']:<8} "
                  f"{enab:<9} {prec} {rec}  sha={sha}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    n = build_registry()
    print(f"Registry synced: {n} rows upserted.")
    print_registry_summary()
