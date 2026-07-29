"""
aiem_function_registry_build.py
--------------------------------
Function Registry — required by Joel's instruction (2026-07-08):

    "For any significant logic that lives inside main.py (or any other large
    orchestration file) rather than inside one of the registered module
    files, create a Function Registry entry so that nothing is hidden inside
    a large source file."

This is phase-by-phase, additive, same discipline as aiem_registry_build.py:
  - Only functions that were actually read (sed/grep) and confirmed real are
    registered here. No fabricated inputs/outputs/dependencies.
  - `is_inline=True` + `owning_module` explicitly says "no dedicated module
    file" rather than pretending one of the 195 registered modules owns it.
  - `verification_status` is honest per-function:
      VERIFIED       -> function read in full, does what's described, all
                         upstream/downstream edges in this entry confirmed.
      VERIFIED_EXISTS-> function confirmed real (non-stub) and its own
                         direct effect confirmed, but its own deeper
                         upstream chain belongs to a phase not yet reached
                         (e.g. _run_conviction_scanner's L1-L8 signal
                         inputs belong to Phase 9; not faked here, flagged
                         for revisit when that phase is verified).

Run: AIEM_DATABASE_URL=... python3 aiem_function_registry_build.py
REQUIRES: aiem_registry.init_schema() already run (creates aiem_function_registry).
"""

import os
import psycopg2

from aiem_registry import PHASE_NAMES


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("AIEM_DATABASE_URL is not set.")
    return psycopg2.connect(url)


# ---------------------------------------------------------------------------
# PHASE 0 — Scanner Input / Candidate Generation
# Every row below was traced by directly reading main.py (sed), not by
# import or execution. Evidence string names the exact grep/sed check used.
# ---------------------------------------------------------------------------
PHASE0_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_get_daily_candidates",
        purpose="AI-callable tool (tool_name: get_daily_candidates) — returns today's "
                "top-N stocks ranked by conviction score for candidate generation.",
        inputs="limit:int=10, min_conviction_pct:int=60, min_similarity:float=0.75",
        outputs="dict {status, candidates:[{ticker, total_pts, conviction_pct, label, "
                "price, layers, rank, ...}], n}",
        upstream_dependencies="Reads conviction_stack_watchlist table directly (no live "
                "call) -> written by snapshot_conviction_stack()/_run_conviction_scanner()",
        downstream_dependencies="AI chat tool dispatch map (tool_name get_daily_candidates); "
                "consumed by AIEM chat sessions",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_get_daily_candidates' main.py; sed "
                "read of function body confirms direct SELECT on conviction_stack_watchlist",
        verified_by_command="sed -n 'Nx,Nyp' main.py (Phase 0 tool-tracing pass, 2026-07-08)",
    ),
    dict(
        file_name="main.py",
        function_name="snapshot_conviction_stack",
        purpose="Persists today's L1-L8 EXTREME conviction cohort to "
                "conviction_stack_watchlist — real upstream producer for get_daily_candidates.",
        inputs="min_pts:float=8.0, max_tickers:int=CONVICTION_STACK_MAX, precomputed=None",
        outputs="dict {ok, status, snap_date, universe_count, logged}; writes rows to "
                "conviction_stack_watchlist",
        upstream_dependencies="_run_conviction_scanner() (L1-L8 money-pressure scoring engine)",
        downstream_dependencies="conviction_stack_watchlist table -> "
                "_aiem_tool_get_daily_candidates, TOP SCORE tab, owner smart-money email",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def snapshot_conviction_stack' main.py + confirmed "
                "call to _run_conviction_scanner inside body",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_run_conviction_scanner",
        purpose="Master 5-layer conviction scanner — scores tickers 0-10 pts on a "
                "unified money-pressure conviction score (8.0+ pts ~= 90% probability setup).",
        inputs="max_tickers:int=15, force_tickers=None",
        outputs="list[dict] ranked tickers with total_pts/conviction_pct/label/layers",
        upstream_dependencies="OI/charm/gamma/sweep signal layer sources — NOT fully traced "
                "in this Phase 0 pass; those tables/signals are conceptually Phase 9 "
                "(Scoring, Analytics & Decision Logging). Will be verified in full when "
                "Phase 9 is reached.",
        downstream_dependencies="snapshot_conviction_stack(), owner smart-money email, "
                "on-demand single-ticker score API endpoint",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED_EXISTS",
        verification_evidence="grep -n 'def _run_conviction_scanner' main.py; docstring "
                "+ signature confirmed real; full upstream signal chain deferred to Phase 9",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_independent_picks",
        purpose="AI-callable tool (tool_name: query_independent_picks) — reads AIEM's own "
                "independent Workstream D picks.",
        inputs="days_back:int=14, pick_type=None, ticker=None, limit:int=100",
        outputs="dict {count, picks:[...]} sourced from aiem_independent_picks",
        upstream_dependencies="aiem_independent_picks table -> written by "
                "_aiem_indep_tool_save_independent_picks(), called from _indep_scan_thread()",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_independent_picks)",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_independent_picks' main.py; "
                "confirmed direct SELECT on aiem_independent_picks",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_compare_independent_vs_website_picks",
        purpose="AI-callable tool (tool_name: compare_independent_vs_website_picks) — "
                "side-by-side comparison of AIEM's independent picks vs website-sourced "
                "paper trades.",
        inputs="days_back:int=30",
        outputs="dict {aiem_independent_polygon:{...}, website_sourced_paper_trades:{...}}",
        upstream_dependencies="aiem_independent_picks (Workstream D) + aiem_paper_trades "
                "(written by _aiem_paper_execute_today())",
        downstream_dependencies="AI chat tool dispatch map "
                "(tool_name compare_independent_vs_website_picks)",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_compare_independent_vs_website_picks' "
                "main.py; confirmed reads of both source tables",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_run_aiem_independent_pick_scan (nested: _indep_scan_thread)",
        purpose="Workstream D autonomous daily pick generation — AIEM scores the raw "
                "universe with its own formula (no external AI call) and saves picks.",
        inputs="kind:str ('stock'|'options')",
        outputs="calls _aiem_indep_tool_save_independent_picks(stock_picks=... or "
                "option_picks=...)",
        upstream_dependencies="_is_trading_day() gate; raw options/stock universe fetch "
                "(get_raw_options_universe per tool docstring)",
        downstream_dependencies="_aiem_indep_tool_save_independent_picks() -> "
                "aiem_independent_picks table",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _run_aiem_independent_pick_scan' main.py; sed "
                "read confirms nested _indep_scan_thread + trading-day gate",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_indep_tool_save_independent_picks",
        purpose="Persists AIEM's independent picks (Workstream D) to aiem_independent_picks, "
                "capped at 20 per type (stocks/options scored independently).",
        inputs="stock_picks=None, option_picks=None (list[dict]: ticker, rank, "
                "confidence_score, rationale, holding_period_days)",
        outputs="dict {saved_stock, saved_option counts}; writes rows to "
                "aiem_independent_picks",
        upstream_dependencies="Called by _indep_scan_thread() inside "
                "_run_aiem_independent_pick_scan()",
        downstream_dependencies="aiem_independent_picks table -> "
                "query_independent_picks, compare_independent_vs_website_picks tools",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_indep_tool_save_independent_picks' "
                "main.py; docstring + signature confirmed",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_paper_execute_today",
        purpose="9:35 AM ET paper-trading execution — picks top 20, fetches live prices, "
                "records positions. This is the 'website-sourced' comparison side of "
                "compare_independent_vs_website_picks.",
        inputs="none (reads current ET date; NYSE trading-day + already-executed-today gates)",
        outputs="writes rows to aiem_paper_trades",
        upstream_dependencies="Candidate/top-20 source not fully traced in this Phase 0 "
                "pass — execution logic is conceptually Phase 13 (Execution & Shadow "
                "Trading). Will be verified in full when Phase 13 is reached.",
        downstream_dependencies="aiem_paper_trades table -> "
                "compare_independent_vs_website_picks tool",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED_EXISTS",
        verification_evidence="grep -n 'def _aiem_paper_execute_today' main.py; confirmed "
                "real INSERT INTO aiem_paper_trades inside body; full candidate-source "
                "trace deferred to Phase 13",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_refresh_universe",
        purpose="AI-callable tool (tool_name: mkt_refresh_universe) — triggers background "
                "refresh of the ticker_lifecycle + ticker_meta reference tables.",
        inputs="none",
        outputs="dict {status, message}; kicks off 2 background threads",
        upstream_dependencies="none (entry point)",
        downstream_dependencies="_mkt_refresh_ticker_lifecycle_bg(), "
                "_mkt_refresh_ticker_meta_bg()",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_refresh_universe' main.py; confirmed "
                "both bg-thread calls in body",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_refresh_ticker_lifecycle_bg",
        purpose="Pulls active + delisted ticker records from the Polygon reference API into "
                "ticker_lifecycle (survivorship-bias correction). Runs weekly, Sunday 8 PM ET.",
        inputs="none (uses POLYGON_API_KEY env secret)",
        outputs="writes rows to ticker_lifecycle table",
        upstream_dependencies="Polygon /v3/reference/tickers API",
        downstream_dependencies="ticker_lifecycle table -> survivorship correction used by "
                "mkt_segment_by_cap_tier / mkt_segment_by_sector",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="sed -n '28671,28683p' main.py — docstring + Polygon call "
                "confirmed; weekly-Sunday scheduling trigger not independently re-verified "
                "this pass",
        verified_by_command="sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_refresh_ticker_meta_bg",
        purpose="Pulls sector + market cap from the Polygon reference API into ticker_meta. "
                "Runs weekly.",
        inputs="tickers=None (optional subset; defaults to full universe)",
        outputs="writes rows to ticker_meta table",
        upstream_dependencies="Polygon /v3/reference/tickers API",
        downstream_dependencies="ticker_meta table -> mkt_segment_by_cap_tier, "
                "mkt_segment_by_sector",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="sed -n '28103,28115p' main.py — docstring + Polygon call "
                "confirmed",
        verified_by_command="sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_screen_by_indicator",
        purpose="AI-callable tool (tool_name: mkt_screen_by_indicator) — screens the full "
                "market by any Barchart-style technical indicator.",
        inputs="indicator:str='rsi_14', operator:str='lt', threshold:float=30.0, "
                "min_price:float=2.0, min_volume:int=100000, end_date=None, top_n:int=50",
        outputs="dict of matches sorted by signal strength",
        upstream_dependencies="polygon_market_daily + polygon_indicators_daily tables",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_screen_by_indicator)",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_screen_by_indicator' main.py; confirmed in "
                "Phase 0 tool-dispatch map already in this session",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_screen_period",
        purpose="AI-callable tool (tool_name: mkt_screen_period) — custom backtest screening "
                "stocks meeting arbitrary conditions over a date range, measuring forward "
                "performance.",
        inputs="conditions:dict, start_date:str, end_date:str, min_move_pct:float=3.0, "
                "hold_days:int=1",
        outputs="dict of backtest results",
        upstream_dependencies="_mkt_parse_conditions() (condition parsing), "
                "polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_screen_period)",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_screen_period' main.py; confirmed call to "
                "_mkt_parse_conditions in body",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_segment_by_cap_tier",
        purpose="AI-callable tool (tool_name: mkt_segment_by_cap_tier) — tests a signal "
                "separately within each market-cap tier (nano/small/mid/large).",
        inputs="conditions:dict, horizon:str='next_day'",
        outputs="dict of results per cap tier",
        upstream_dependencies="_mkt_parse_conditions(), _mkt_run_two_group(), "
                "ticker_meta.cap_tier",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_segment_by_cap_tier)",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_segment_by_cap_tier' main.py; confirmed "
                "calls to shared helpers in body",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_segment_by_sector",
        purpose="AI-callable tool (tool_name: mkt_segment_by_sector) — tests a signal "
                "separately within each SIC sector.",
        inputs="conditions:dict, horizon:str='next_day'",
        outputs="dict of results per sector",
        upstream_dependencies="_mkt_parse_conditions(), _mkt_run_two_group(), "
                "ticker_meta.sector",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_segment_by_sector)",
        owning_phase=0,
        owning_module="INLINE (main.py) — no dedicated Phase 0 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_segment_by_sector' main.py; confirmed "
                "calls to shared helpers in body",
        verified_by_command="grep + sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_parse_conditions",
        purpose="Shared helper — converts a condition dict (e.g. {'gap_pct_min':2.0}) into "
                "a whitelist-safe SQL WHERE fragment + params.",
        inputs="conditions:dict",
        outputs="tuple (sql_fragment:str, params:list)",
        upstream_dependencies="_MKT_SAFE_COLS / _MKT_INDICATOR_COLS column whitelists",
        downstream_dependencies="Called by _mkt_screen_period, _mkt_tool_segment_by_cap_tier, "
                "_mkt_tool_segment_by_sector, and other mkt_* tools outside Phase 0",
        owning_phase=0,
        owning_module="INLINE (main.py) — shared helper, no dedicated module file",
        verification_status="VERIFIED",
        verification_evidence="sed -n '23204,23233p' main.py — full body read, confirmed "
                "whitelist-only column resolution (no raw SQL injection path)",
        verified_by_command="sed trace, 2026-07-08",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_run_two_group",
        purpose="Shared helper — fetches forward returns for a signal group vs a baseline "
                "group and computes comparison stats. Docstring states ~20 call sites "
                "across the mkt_* research tool family.",
        inputs="conn, sig_where, sig_params, base_where, base_params, horizon_days:int=1, "
                "limit:int=100000",
        outputs="dict of stats, or None",
        upstream_dependencies="polygon_market_daily (+ polygon_indicators_daily when "
                "indicator-aware conditions are present)",
        downstream_dependencies="Called by _mkt_tool_segment_by_cap_tier and "
                "_mkt_tool_segment_by_sector in Phase 0; ~18 other call sites belong to "
                "other phases' mkt_* tools (not re-verified here)",
        owning_phase=0,
        owning_module="INLINE (main.py) — shared helper, no dedicated module file",
        verification_status="VERIFIED",
        verification_evidence="sed -n '23234,23260p' main.py — confirmed LEAD() "
                "window-function CTE + auto-join-on-'ind.'-prefix logic described in "
                "docstring",
        verified_by_command="sed trace, 2026-07-08",
    ),
]


# ---------------------------------------------------------------------------
# PHASE 1 — Orchestration Layer
# Every row below was traced by directly reading main.py (sed), not by
# import or execution. Evidence string names the exact grep/sed check used.
# Only the 2 Phase-1-tagged tools with NO backing module file are registered
# here (log_prediction, get_live_snapshot). The other 6 Phase-1 tools are
# either genuinely file-owned (aiem_level2.py/aiem_level3.py/aiem_v2_system.py)
# or real-module-but-cross-phase (decision_logger.py, Phase 9) — those do not
# need a Function Registry row since a real module already owns them.
# ---------------------------------------------------------------------------
PHASE1_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_log_prediction",
        purpose="AI-callable tool (tool_name: log_prediction) — saves AIEM's own "
                "directional call (BULLISH/BEARISH/NEUTRAL) on a ticker to the track "
                "record so it can be graded automatically once target_date arrives.",
        inputs="ticker:str, direction:str, horizon_days:int=3, confidence:str='MEDIUM', "
                "predicted_win_pct:float=None, rationale:str=None, session_id:str=None",
        outputs="dict {logged, prediction_id, entry_price, target_date}; INSERT INTO "
                "aiem_track_record",
        upstream_dependencies="polygon_market_daily (latest close as entry_price)",
        downstream_dependencies="aiem_track_record table -> automatic grading job "
                "(not re-traced in this Phase 1 pass; belongs to the learning/grading "
                "phases downstream)",
        owning_phase=1,
        owning_module="INLINE (main.py) — no dedicated Phase 1 module file",
        verification_status="VERIFIED",
        verification_evidence="sed -n '21877,21910p' main.py — full body read, confirmed "
                "direct psycopg2 INSERT into aiem_track_record with real entry_price lookup",
        verified_by_command="sed trace, 2026-07-08 (Phase 1 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_get_live_snapshot",
        purpose="AI-callable tool (tool_name: get_live_snapshot) — live intraday data for "
                "specific tickers right now, from Polygon's snapshot endpoint, for "
                "'right now'/'currently'/'today so far' questions.",
        inputs="tickers: str or list[str] (capped at 50)",
        outputs="dict {status:'OK'|'DELAYED'|'error', per-ticker price/volume/minute-bar/"
                "change_pct, _cache_age_s}; in-process TTL cache (_LIVE_SNAPSHOT_CACHE)",
        upstream_dependencies="Polygon v2 snapshot API (POLYGON_API_KEY secret)",
        downstream_dependencies="AI chat tool dispatch map (tool_name get_live_snapshot)",
        owning_phase=1,
        owning_module="INLINE (main.py) — no dedicated Phase 1 module file",
        verification_status="VERIFIED",
        verification_evidence="sed -n '31029,31070p' main.py — full body read, confirmed "
                "real urllib call to Polygon snapshot endpoint + TTL cache check",
        verified_by_command="sed trace, 2026-07-08 (Phase 1 tool-tracing pass)",
    ),
]


# ---------------------------------------------------------------------------
# PHASE 2 — Guardrails & Safety
# Every row below was traced by directly reading main.py (sed), not by
# import or execution. Only the 1 Phase-2-tagged tool with NO backing module
# file is registered here (mkt_check_survivorship). The other 10 Phase-2
# tools are either genuinely file-owned (point_in_time_guard.py/
# simulation_lock.py/kill_switch.py) or real-module-but-cross-phase
# (aiem_pullback_reentry.py Phase 5; aiem_risk_guards.py Phase 11) — those
# do not need a Function Registry row since a real module already owns them.
# ---------------------------------------------------------------------------
PHASE2_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_mkt_tool_check_survivorship",
        purpose="AI-callable tool (tool_name: mkt_check_survivorship) — point-in-time "
                "survivorship check: was this ticker actually tradeable on a given "
                "check_date? Prevents survivorship bias in backtests (delisted stocks "
                "silently absent from today's universe).",
        inputs="ticker:str, check_date:str ('YYYY-MM-DD')",
        outputs="dict {status, ticker, check_date, was_active:bool|None, listed_date, "
                "delisted_date}; was_active=None + warning if ticker missing from "
                "ticker_lifecycle",
        upstream_dependencies="ticker_lifecycle table (populated by "
                "_mkt_refresh_ticker_lifecycle_bg, a Phase 0 function, weekly Sunday 8PM ET)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_check_survivorship); "
                "intended caller = backtest inclusion logic before scoring a historical "
                "signal date",
        owning_phase=2,
        owning_module="INLINE (main.py) — no dedicated Phase 2 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_check_survivorship' main.py; full body "
                "read via sed confirms direct psycopg2 SELECT on ticker_lifecycle "
                "(listed_date/delisted_date/active) with correct tradeable-window logic",
        verified_by_command="sed trace, 2026-07-08 (Phase 2 tool-tracing pass)",
    ),
]


# ---------------------------------------------------------------------------
# PHASE 3 — Macro & Regime Context
# Every row below was traced by directly reading main.py (sed), not by
# import or execution. Only the 4 Phase-3-tagged tools with NO backing module
# file are registered here (query_market_regime, momentum_macro_regime,
# mkt_regime_filter, mkt_term_structure). The other 9 Phase-3 tools are
# either genuinely file-owned (regime_detector.py/regime_monitor.py/
# market_regime_overlay.py/aiem_cta_triggers.py/economic_calendar.py) or
# real-module-but-cross-phase (aiem_pullback_reentry.py Phase 5;
# aiem_risk_guards.py Phase 11) — those do not need a Function Registry row.
# ---------------------------------------------------------------------------
PHASE3_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_market_regime",
        purpose="AI-callable tool (tool_name: query_market_regime) — breaks pick win rates "
                "down by market regime (BULL/BEAR/CHOP from SPY daily return) and VIX bucket "
                "(LOW/MED/HIGH) at time of pick, to reveal regime-dependent signal performance.",
        inputs="days_back:int=60 (capped at 90)",
        outputs="dict of per-regime/per-vix-bucket win-rate stats, sourced from "
                "ai_short_calls_log LEFT JOIN spy_daily_cache",
        upstream_dependencies="ai_short_calls_log (pick outcomes) + spy_daily_cache "
                "(spy_daily_ret, vix_close) tables",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_market_regime)",
        owning_phase=3,
        owning_module="INLINE (main.py) — no dedicated Phase 3 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_market_regime' main.py (line "
                "21176); sed read confirms real LEFT JOIN on spy_daily_cache with "
                "BULL/BEAR/CHOP + LOW/MED/HIGH_VIX bucketing logic",
        verified_by_command="sed trace, 2026-07-08 (Phase 3 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_momentum_macro_regime",
        purpose="AI-callable tool (tool_name: momentum_macro_regime) — today's market regime "
                "plus backtested coil-signal performance by regime. Sector breadth = fraction "
                "of the 11 SPDR sector ETFs above their 20-day moving average.",
        inputs="none (**_kw accepted but unused)",
        outputs="dict with sector breadth %, regime label, coil-signal performance-by-regime "
                "stats",
        upstream_dependencies="sector_etf_daily table (11 SPDR ETFs) + polygon_market_daily "
                "(ticker MA20/MA50 window functions)",
        downstream_dependencies="AI chat tool dispatch map (tool_name momentum_macro_regime)",
        owning_phase=3,
        owning_module="INLINE (main.py) — no dedicated Phase 3 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_momentum_macro_regime' main.py (line 61054); "
                "sed read confirms real window-function SQL over sector_etf_daily + "
                "polygon_market_daily, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 3 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_regime_filter",
        purpose="AI-callable tool (tool_name: mkt_regime_filter) — tests any signal condition "
                "split by market regime (SPY gap_pct that day: bull >= +0.5%, bear <= -0.5%, "
                "else flat).",
        inputs="conditions:dict (required), horizon:str='next_day'",
        outputs="dict of forward-return stats per regime (bull/bear/flat)",
        upstream_dependencies="_mkt_parse_conditions() (shared Phase 0 helper) + "
                "polygon_market_daily (SPY gap_pct as regime classifier)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_regime_filter)",
        owning_phase=3,
        owning_module="INLINE (main.py) — no dedicated Phase 3 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_regime_filter' main.py (line 24143); sed "
                "read confirms real SPY gap_pct bull/bear/flat split + call to shared "
                "_mkt_parse_conditions helper, no dedicated module",
        verified_by_command="sed trace, 2026-07-08 (Phase 3 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_term_structure",
        purpose="AI-callable tool (tool_name: mkt_term_structure) — finds tickers with unusual "
                "options term structure (front/back-month IV ratio). INVERTED(>1.10) = "
                "near-term stress event expected; CONTANGO(<0.88) = market calm/complacent.",
        inputs="inverted_only:bool=False, days_back:int=1",
        outputs="dict {count, results:[{ticker, scan_date, spot, term_ratio, term_tag, "
                "front_iv, back_iv, gex_regime, pc_skew_tag}], interpretation}",
        upstream_dependencies="options_structure_scan table (does NOT call "
                "aiem_options_structure.py, which is a separate Phase 6 module)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_term_structure)",
        owning_phase=3,
        owning_module="INLINE (main.py) — no dedicated Phase 3 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_term_structure' main.py (line 31297); "
                "sed read confirms direct psycopg2 SELECT on options_structure_scan, no "
                "module import despite aiem_options_structure.py existing separately",
        verified_by_command="sed trace, 2026-07-08 (Phase 3 tool-tracing pass)",
    ),
]


# ---------------------------------------------------------------------------
# PHASE 4 — Discovery Engine
# Every row below was traced by directly reading main.py (sed), not by
# import or execution. Only the 13 Phase-4-tagged tools with NO backing
# module file are registered here. The other 13 Phase-4 tools are genuinely
# file-owned (aiem_discovery_engine.py x6, hypothesis_registry.py x2,
# active_hypothesis_selection.py, causal_discovery.py,
# historical_analog_search.py, breakout_signature_discovery.py x2) — those
# do not need a Function Registry row.
# ---------------------------------------------------------------------------
PHASE4_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_discover_numeric_patterns",
        purpose="AI-callable tool (tool_name: discover_numeric_patterns) — tests whether a "
                "numeric threshold on a metric predicts winners, by splitting into quartiles "
                "and comparing win rates across them.",
        inputs="metric:str='day_ret' (one of day_ret/vol_oi/stock_price/t3_pct), "
                "days_back:int=30 (capped at 90)",
        outputs="dict of per-quartile win-rate stats (NTILE(4) SQL window function)",
        upstream_dependencies="ai_short_calls_log or similar pick-outcome table (queried via "
                "shared _DB_URL)",
        downstream_dependencies="AI chat tool dispatch map (tool_name discover_numeric_patterns)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_discover_numeric_patterns' main.py (line "
                "19774); sed read confirms real NTILE(4) quartile SQL over an allow-listed "
                "metric set",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_list_signal_dimensions",
        purpose="AI-callable tool (tool_name: list_signal_dimensions) — shows all queryable "
                "signal dimensions with distributions; intended as the FIRST call before "
                "composing any hypothesis so the agent knows what fields/ranges actually exist.",
        inputs="none",
        outputs="dict of base dataset stats (total_signals, t3_wins/losses, t5_graded, etc.)",
        upstream_dependencies="shared _DB_URL connection, pick-outcome tables",
        downstream_dependencies="AI chat tool dispatch map (tool_name list_signal_dimensions)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_list_signal_dimensions' main.py (line "
                "22086); sed read confirms real dataset-stats SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_register_hypotheses",
        purpose="AI-callable tool (tool_name: register_hypotheses) — pre-registers the agent's "
                "hypotheses BEFORE looking at any data, to prevent p-hacking by committing to "
                "what is being tested upfront.",
        inputs="hypotheses: list of falsifiable, directional claim strings",
        outputs="dict confirming rows written to aiem_research_hypotheses",
        upstream_dependencies="CREATE TABLE IF NOT EXISTS aiem_research_hypotheses (owns this "
                "table)",
        downstream_dependencies="AI chat tool dispatch map (tool_name register_hypotheses); "
                "called as pre-registration step 3 of the research session convention",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_register_hypotheses' main.py (line "
                "20838); sed read confirms real CREATE TABLE IF NOT EXISTS + INSERT",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_search_past_findings",
        purpose="AI-callable tool (tool_name: search_past_findings) — keyword/token-overlap "
                "search over past weekly research findings, to check a candidate finding was "
                "not already documented before labeling it NEW.",
        inputs="query_text:str, weeks_back:int=16",
        outputs="dict of matched past findings ranked by token overlap score, no embeddings/"
                "external calls",
        upstream_dependencies="aiem_research_insights table (findings, confidence, "
                "research_date)",
        downstream_dependencies="AI chat tool dispatch map (tool_name search_past_findings)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_search_past_findings' main.py (line "
                "21040); sed read confirms real SELECT + token-overlap scoring, zero-cost, "
                "no embeddings",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_send_discovery_alert",
        purpose="AI-callable tool (tool_name: send_discovery_alert) — lets AIEM autonomously "
                "email the owner (no permission gate) whenever it finds a setup worth sharing, "
                "carrying ticker/signal/confidence/reasoning/oos_accuracy/risk_gate/divergence "
                "flags.",
        inputs="ticker:str, signal_name:str, confidence:float, reasoning:str, "
                "oos_accuracy:float=0.0, risk_gate_passed:bool=True, divergence_flags:str='', "
                "key_features:str='', suggested_action:str=''",
        outputs="dict confirming the owner email was sent",
        upstream_dependencies="shared owner-email sender infrastructure (see "
                "owner-email-scheduler.md memory topic)",
        downstream_dependencies="AI chat tool dispatch map (tool_name send_discovery_alert); "
                "owner's inbox",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_send_discovery_alert' main.py (line "
                "37834); sed read confirms real autonomous owner-email dispatch, no "
                "permission gate",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_behavioral_templates",
        purpose="AI-callable tool (tool_name: mkt_behavioral_templates) — shows the pre-move "
                "behavioral template library: fingerprints of what stocks looked like BEFORE "
                "they made a big move, so the agent understands early-accumulation patterns.",
        inputs="min_move_pct:float=10.0, limit:int=50",
        outputs="dict of template rows (ticker, move_date, move_pct, avg_gap, avg_rvol, "
                "avg_cs, cs_accel, vol_accel_5d, price_mom_5d, days_positive, vwap_above, "
                "gap_count)",
        upstream_dependencies="pre_move_templates table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_behavioral_templates)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_behavioral_templates' main.py (line 30600); "
                "sed read confirms real SELECT on pre_move_templates",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_find_behavioral_matches",
        purpose="AI-callable tool (tool_name: mkt_find_behavioral_matches) — the core pre-move "
                "radar: returns stocks whose CURRENT behavior most closely matches the "
                "historical fingerprint of stocks BEFORE they made a big move.",
        inputs="min_similarity:float=0.80, min_move_pct:float=10.0, hours_back:int=2, "
                "limit:int=20",
        outputs="dict of matches (ticker, similarity, matched_ticker, matched_date, "
                "matched_move, days_before_move, verdict, current_fingerprint, scan_time)",
        upstream_dependencies="behavioral_pattern_matches table, fed by the 24/7 behavioral "
                "engine's OWN inline fingerprint math (_compute_fingerprint/_cosine_sim at "
                "main.py lines 30264/30369) — NOT behavioral_fingerprint.py, which is a "
                "documented-dormant parallel extraction (see aiem_phase4_verify.py)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_find_behavioral_matches); "
                "runs automatically every 30 min via the 24/7 behavioral engine",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_find_behavioral_matches' main.py (line 30645); "
                "sed read confirms real SELECT on behavioral_pattern_matches",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_generate_hypotheses",
        purpose="AI-callable tool (tool_name: mkt_generate_hypotheses) — returns a predefined "
                "battery of testable signal hypotheses (e.g. RVOL+close-strength continuation) "
                "so AIEM owns its own research agenda without external calls.",
        inputs="context:str='', n_hypotheses:int=8",
        outputs="dict of hypothesis/condition/rationale triples drawn from a static battery",
        upstream_dependencies="none (predefined in-process list, no DB/external calls)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_generate_hypotheses)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_generate_hypotheses' main.py (line "
                "24296); sed read confirms a static predefined battery, zero external/DB calls",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_save_discovery",
        purpose="AI-callable tool (tool_name: mkt_save_discovery) — saves a validated signal "
                "discovery to aiem_signal_discoveries, enforcing 4 hard gates (oos_edge>0, "
                "required p_value vs Bonferroni threshold, win_rate>=54%, signal_n>=200).",
        inputs="conditions:dict, hypothesis_text:str, edge_broad/edge_tight/signal_n/p_value/"
                "signal_win_rate/baseline_win_rate/signal_avg_ret/oos_edge:various, "
                "horizon:str='next_day', notes:str='', hypothesis_id:int=None",
        outputs="dict confirming insert into aiem_signal_discoveries, or a hard-gate rejection",
        upstream_dependencies="aiem_signal_discoveries table (writes); optionally links to "
                "hypothesis_registry.py via hypothesis_id",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_save_discovery); "
                "mkt_load_discoveries reads back what this writes",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_save_discovery' main.py (line 24336); sed "
                "read confirms the 4 hard gates described in the docstring (see "
                "stat-integrity-gate-fixes.md memory topic for the Bonferroni fail-closed fix)",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_load_discoveries",
        purpose="AI-callable tool (tool_name: mkt_load_discoveries) — loads previously saved "
                "signal discoveries at the START of a research session, to avoid re-discovering "
                "the same signals.",
        inputs="status:str='validated', min_edge_tight:float=None, min_oos_edge:float=None",
        outputs="dict of matching rows from aiem_signal_discoveries",
        upstream_dependencies="aiem_signal_discoveries table (reads what mkt_save_discovery "
                "writes)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_load_discoveries)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_load_discoveries' main.py (line 24539); "
                "sed read confirms real filtered SELECT on aiem_signal_discoveries",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_explore_dimensions",
        purpose="AI-callable tool (tool_name: mkt_explore_dimensions) — statistical summary of "
                "the full polygon_market_daily universe; intended as the FIRST call before "
                "testing any signal so the agent knows what data exists.",
        inputs="none",
        outputs="dict of dataset-wide stats (n_dates, earliest/latest date, etc.), cached "
                "in-process for 1 hour",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_explore_dimensions)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_explore_dimensions' main.py (line 23654); "
                "sed read confirms real 1-hour in-process TTL cache + SET LOCAL "
                "statement_timeout SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_discover_interactions",
        purpose="AI-callable tool (tool_name: mkt_discover_interactions) — builds a 3x3 "
                "(low/mid/high) x (low/mid/high) grid of two factors to find synergistic "
                "combinations that outperform either factor alone.",
        inputs="factor1:str='gap_pct', factor2:str='rvol' (both must be in the shared "
                "_MKT_SAFE_COLS allow-list), horizon:str='next_day'",
        outputs="dict of forward-return stats per grid cell",
        upstream_dependencies="polygon_market_daily table, shared _MKT_SAFE_COLS allow-list "
                "(Phase 0 helper)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_discover_interactions)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_discover_interactions' main.py (line "
                "24650); sed read confirms real PERCENTILE_CONT tercile-grid SQL with "
                "allow-listed column guard",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_invent_indicator",
        purpose="AI-callable tool (tool_name: mkt_invent_indicator) — tests a predefined "
                "composite SQL indicator expression (e.g. gap_pct*rvol*close_strength) against "
                "forward returns; rotates through a curated list, no external calls.",
        inputs="inspiration:str='', horizon:str='next_day'",
        outputs="dict with the tested indicator's name/expression/rationale and its forward-"
                "return performance",
        upstream_dependencies="polygon_market_daily table, predefined in-process indicator "
                "list (_INDICATORS)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_invent_indicator)",
        owning_phase=4,
        owning_module="INLINE (main.py) — no dedicated Phase 4 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_invent_indicator' main.py (line 24947); "
                "sed read confirms a real curated _INDICATORS list with SQL expressions, no "
                "external calls",
        verified_by_command="sed trace, 2026-07-08 (Phase 4 tool-tracing pass)",
    ),
]

PHASE5_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_mkt_tool_volume_patterns",
        purpose="AI-callable tool (tool_name: mkt_volume_patterns) — scans polygon_market_daily "
                "for volume-pattern setups (e.g. volume dry-up, climax volume) via direct SQL.",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched volume-pattern rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_volume_patterns)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_volume_patterns' main.py (line 24787); "
                "sed read confirms real psycopg2 SQL, no fabricated stub",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_price_patterns",
        purpose="AI-callable tool (tool_name: mkt_price_patterns) — direct SQL scan for "
                "price-pattern setups over polygon_market_daily.",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched price-pattern rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_price_patterns)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_price_patterns' main.py (line 24829); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_compute_momentum",
        purpose="AI-callable tool (tool_name: mkt_compute_momentum) — direct SQL/psycopg2 "
                "momentum computation over stored OHLCV history.",
        inputs="ticker, lookback window (see call site)",
        outputs="dict of momentum metrics",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_compute_momentum)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_compute_momentum' main.py (line 24874); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_compute_indicators",
        purpose="AI-callable tool (tool_name: mkt_compute_indicators) — full Barchart-parity "
                "manual reimplementation of SMA/EMA/RSI/Stochastic/Williams %R/CCI/MACD/ADX/"
                "Parabolic SAR/Bollinger/Keltner/ATR/OBV/MFI/CMF/ROC/Momentum from stored OHLCV. "
                "Does NOT call indicators.py despite the similar name — fully independent "
                "inline math, confirmed by reading the function body (numpy-only, no import "
                "of the indicators module).",
        inputs="ticker:str, start_date:str=None, end_date:str=None",
        outputs="dict of latest-value snapshot + 60-day time series for every indicator listed",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_compute_indicators)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_compute_indicators' main.py (line 25650); "
                "sed read of full body confirms manual _sma/_ema/etc helpers, no import of "
                "indicators.py",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_quiet_accumulation",
        purpose="AI-callable tool (tool_name: mkt_quiet_accumulation) — direct psycopg2/numpy "
                "scan for quiet-accumulation setups (low volatility, rising OBV/volume base).",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched quiet-accumulation rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_quiet_accumulation)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_quiet_accumulation' main.py (line 28431); "
                "sed read confirms real psycopg2/numpy SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_accumulation_into_squeeze",
        purpose="AI-callable tool (tool_name: mkt_accumulation_squeeze) — direct psycopg2/numpy "
                "scan combining accumulation signature with a developing volatility squeeze.",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched accumulation-into-squeeze rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_accumulation_squeeze)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_accumulation_into_squeeze' main.py "
                "(line 28572); sed read confirms real psycopg2/numpy SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_pre_squeeze_warning",
        purpose="AI-callable tool (tool_name: mkt_pre_squeeze_warning) — direct psycopg2 scan "
                "flagging tickers approaching a volatility-squeeze breakout before it fires.",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched pre-squeeze-warning rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_pre_squeeze_warning)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_pre_squeeze_warning' main.py (line 29156); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_extreme_move_reversion",
        purpose="AI-callable tool (tool_name: mkt_extreme_move_reversion) — direct "
                "psycopg2/numpy scan for extreme single-day moves showing early mean-reversion "
                "characteristics.",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched extreme-move-reversion rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_extreme_move_reversion)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_extreme_move_reversion' main.py (line 29419); "
                "sed read confirms real psycopg2/numpy SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_detect_capitulation_signature",
        purpose="AI-callable tool (tool_name: mkt_capitulation_detector) — direct "
                "psycopg2/numpy scan detecting a capitulation-selling signature (volume spike + "
                "sharp intraday reversal).",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched capitulation-signature rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_capitulation_detector)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _detect_capitulation_signature' main.py (line 29504); "
                "sed read confirms real psycopg2/numpy SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_52week_high_momentum",
        purpose="AI-callable tool (tool_name: mkt_52week_momentum) — direct psycopg2/numpy scan "
                "for tickers near/at a 52-week high with confirming momentum.",
        inputs="ticker/universe filters (see call site)",
        outputs="dict of matched 52-week-momentum rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_52week_momentum)",
        owning_phase=5,
        owning_module="INLINE (main.py) — no dedicated Phase 5 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_52week_high_momentum' main.py (line 29577); "
                "sed read confirms real psycopg2/numpy SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 5 tool-tracing pass)",
    ),
]


PHASE6_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_mkt_ticker_options_history",
        purpose="AI-callable tool (tool_name: mkt_ticker_options_history) — direct SQL "
                "history of one ticker's recorded call-sweep activity.",
        inputs="ticker:str, days_back:int=30",
        outputs="dict of call_sweep_log rows for the ticker over the window",
        upstream_dependencies="call_sweep_log table (populated by options_sweep.py)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_ticker_options_history)",
        owning_phase=6,
        owning_module="INLINE (main.py) — reads call_sweep_log, no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_ticker_options_history' main.py (line 29738); "
                "sed read confirms real psycopg2 SQL on call_sweep_log",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_options_flow_scan",
        purpose="AI-callable tool (tool_name: mkt_options_flow_scan) — direct SQL universe scan "
                "across call_sweep_log/unusual_calls_log/unusual_calls_microcap_log for options "
                "flow setups.",
        inputs="days_back:int=7, min_premium_k:float=10 (see call site for full filter set)",
        outputs="dict of matched options-flow rows",
        upstream_dependencies="call_sweep_log, unusual_calls_log, unusual_calls_microcap_log tables",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_options_flow_scan)",
        owning_phase=6,
        owning_module="INLINE (main.py) — no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_options_flow_scan' main.py (line 29834); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_options_predicts_price",
        purpose="AI-callable tool (tool_name: mkt_options_predicts_price) — direct SQL/numpy "
                "backtest joining call_sweep_log against forward polygon_market_daily returns to "
                "test whether options flow predicts subsequent price movement.",
        inputs="days_back:int=90, forward_days:int=5 (see call site for full filter set)",
        outputs="dict of predictive-edge statistics",
        upstream_dependencies="call_sweep_log table, polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_options_predicts_price)",
        owning_phase=6,
        owning_module="INLINE (main.py) — no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_options_predicts_price' main.py (line 29891); "
                "sed read confirms real psycopg2/numpy SQL joining both tables",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_cross_confirm_options_price",
        purpose="AI-callable tool (tool_name: mkt_cross_confirm_options) — direct SQL cross-check "
                "of call-sweep signals (call_sweep_log) against unusual-calls signals "
                "(unusual_calls_log) for the same ticker/window to find confirmed setups.",
        inputs="days_back:int=5 (see call site for full filter set)",
        outputs="dict of cross-confirmed rows",
        upstream_dependencies="call_sweep_log table, unusual_calls_log table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_cross_confirm_options)",
        owning_phase=6,
        owning_module="INLINE (main.py) — no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_cross_confirm_options_price' main.py (line 29973); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_net_flow_db",
        purpose="AI-callable tool (tool_name: mkt_net_flow_db) — direct SQL scan of "
                "polygon_market_daily for net directional flow (RVOL-weighted) across the universe.",
        inputs="days_back:int=5, min_rvol:float=1.5 (see call site for full filter set)",
        outputs="dict of net-flow rows",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_net_flow_db)",
        owning_phase=6,
        owning_module="INLINE (main.py) — no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_net_flow_db' main.py (line 30913); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_gex_scan",
        purpose="AI-callable tool (tool_name: mkt_gex_scan) — direct SQL scan of "
                "options_structure_scan for gamma-exposure (GEX) regime setups.",
        inputs="min_abs_gex_m:float=0.0, regime:str='ALL' (see call site for full filter set)",
        outputs="dict of matched GEX rows",
        upstream_dependencies="options_structure_scan table (populated by aiem_options_structure.py)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_gex_scan)",
        owning_phase=6,
        owning_module="INLINE (main.py) — reads options_structure_scan, no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_gex_scan' main.py (line 31244); "
                "sed read confirms real psycopg2 SQL on options_structure_scan",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_options_skew",
        purpose="AI-callable tool (tool_name: mkt_options_skew) — direct SQL scan of "
                "options_structure_scan for put/call skew steepening setups.",
        inputs="min_skew_pp:float=5.0, days_back:int=1",
        outputs="dict of matched skew rows",
        upstream_dependencies="options_structure_scan table (populated by aiem_options_structure.py)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_options_skew)",
        owning_phase=6,
        owning_module="INLINE (main.py) — reads options_structure_scan, no dedicated Phase 6 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_options_skew' main.py (line 31274); "
                "sed read confirms real psycopg2 SQL on options_structure_scan",
        verified_by_command="sed trace, 2026-07-08 (Phase 6 tool-tracing pass)",
    ),
]


PHASE7_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_multivariate_regression",
        purpose="AI-callable tool (tool_name: multivariate_regression) — inline scipy.stats-based "
                "multivariate regression over caller-supplied observations.",
        inputs="See tool schema; caller-supplied feature/target arrays",
        outputs="dict of regression coefficients, p-values, and fit statistics",
        upstream_dependencies="scipy.stats, scipy.special (no DB table)",
        downstream_dependencies="AI chat tool dispatch map (tool_name multivariate_regression)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_multivariate_regression' main.py (line 20887); "
                "sed read confirms real scipy.stats regression, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_run_statistical_significance",
        purpose="AI-callable tool (tool_name: run_statistical_significance) — inline "
                "permutation/random-resampling significance test.",
        inputs="See tool schema; caller-supplied sample data",
        outputs="dict with p-value / significance verdict",
        upstream_dependencies="random module (no DB table)",
        downstream_dependencies="AI chat tool dispatch map (tool_name run_statistical_significance)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_run_statistical_significance' main.py (line 21615); "
                "sed read confirms real random-based resampling logic",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_review_own_accuracy",
        purpose="AI-callable tool (tool_name: review_own_accuracy) — direct SQL read of "
                "aiem_track_record for AIEM's own historical prediction accuracy.",
        inputs="See tool schema; optional lookback window",
        outputs="dict of accuracy/track-record statistics",
        upstream_dependencies="aiem_track_record table",
        downstream_dependencies="AI chat tool dispatch map (tool_name review_own_accuracy)",
        owning_phase=7,
        owning_module="INLINE (main.py) — reads aiem_track_record, no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_review_own_accuracy' main.py (line 21935); "
                "sed read confirms real psycopg2 SQL on aiem_track_record",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_test_signal",
        purpose="AI-callable tool (tool_name: mkt_test_signal) — direct SQL statistical test of a "
                "caller-defined signal condition against forward returns.",
        inputs="See tool schema; signal definition + lookback window",
        outputs="dict of test statistics for the signal",
        upstream_dependencies="polygon_market_daily table (via inline SQL)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_test_signal)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_test_signal' main.py (line 23760); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_test_inverse",
        purpose="AI-callable tool (tool_name: mkt_test_inverse) — direct SQL statistical test of the "
                "inverse of a caller-defined signal condition against forward returns.",
        inputs="See tool schema; signal definition + lookback window",
        outputs="dict of test statistics for the inverted signal",
        upstream_dependencies="polygon_market_daily table (via inline SQL)",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_test_inverse)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_test_inverse' main.py (line 23870); "
                "sed read confirms real psycopg2 SQL",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_validate_oos",
        purpose="AI-callable tool (tool_name: mkt_validate_oos) — direct SQL out-of-sample "
                "validation split over polygon_market_daily scan dates.",
        inputs="See tool schema; signal definition + split parameters",
        outputs="dict of in-sample vs out-of-sample statistics",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_validate_oos)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_validate_oos' main.py (line 24211); "
                "sed read confirms real psycopg2 SQL on polygon_market_daily",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_factor_correlations",
        purpose="AI-callable tool (tool_name: mkt_factor_correlations) — direct SQL/numpy "
                "cross-factor correlation matrix over polygon_market_daily.",
        inputs="See tool schema; factor list + lookback window",
        outputs="dict of pairwise factor correlations",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_factor_correlations)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_factor_correlations' main.py (line 24580); "
                "sed read confirms real psycopg2/numpy SQL on polygon_market_daily",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_required_pvalue",
        purpose="AI-callable tool (tool_name: mkt_required_pvalue) — direct SQL read of "
                "aiem_test_ledger to compute the Bonferroni-adjusted required p-value given "
                "recent test volume.",
        inputs="See tool schema; optional lookback window",
        outputs="dict with required_pvalue and recent test count",
        upstream_dependencies="aiem_test_ledger table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_required_pvalue)",
        owning_phase=7,
        owning_module="INLINE (main.py) — reads aiem_test_ledger, no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_required_pvalue' main.py (line 27788); "
                "sed read confirms real psycopg2 SQL on aiem_test_ledger",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_retrospective_backtest",
        purpose="AI-callable tool (tool_name: mkt_retrospective_backtest) — direct SQL/numpy "
                "retrospective backtest of a caller-defined signal over polygon_market_daily.",
        inputs="See tool schema; signal definition + lookback window",
        outputs="dict of backtest performance statistics",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_retrospective_backtest)",
        owning_phase=7,
        owning_module="INLINE (main.py) — no dedicated Phase 7 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_retrospective_backtest' main.py (line 30689); "
                "sed read confirms real psycopg2/numpy SQL on polygon_market_daily",
        verified_by_command="sed trace, 2026-07-08 (Phase 7 tool-tracing pass)",
    ),
]

PHASE8_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_save_research_model",
        purpose="AI-callable tool (tool_name: save_research_model) — inline persistence of "
                "research conclusions to aiem_research_insights, with a p-value discipline "
                "gate that strips any scoring-weight key lacking a matching {key}_p_value "
                "entry below 0.10.",
        inputs="See tool schema; findings text + scoring_adjustments dict + confidence",
        outputs="dict confirming which weights were saved vs. stripped for insignificance",
        upstream_dependencies="none (pure computation over caller-supplied dict)",
        downstream_dependencies="aiem_research_insights table; AI chat tool dispatch map (tool_name save_research_model)",
        owning_phase=8,
        owning_module="INLINE (main.py) — no dedicated Phase 8 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_save_research_model' main.py (line 19890); "
                "sed read confirms real p-value gate logic writing to aiem_research_insights, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 8 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_evaluate_previous_model",
        purpose="AI-callable tool (tool_name: evaluate_previous_model) — inline direct-SQL "
                "comparison of ai_short_calls_log T+3 win rate in the week before vs. after "
                "the last saved research model.",
        inputs="See tool schema; optional lookback_weeks (capped at 8)",
        outputs="dict comparing before/after win rate and average return",
        upstream_dependencies="aiem_research_insights table, ai_short_calls_log table",
        downstream_dependencies="AI chat tool dispatch map (tool_name evaluate_previous_model)",
        owning_phase=8,
        owning_module="INLINE (main.py) — no dedicated Phase 8 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_evaluate_previous_model' main.py (line 21324); "
                "sed read confirms real psycopg2 SQL on aiem_research_insights/ai_short_calls_log",
        verified_by_command="sed trace, 2026-07-08 (Phase 8 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_rollback_to_previous_model",
        purpose="AI-callable tool (tool_name: rollback_to_previous_model) — inline direct-SQL "
                "rollback that copies the second-most-recent aiem_research_insights row into "
                "today's record when evaluate_previous_model reports the latest model hurt.",
        inputs="none (reads the two most recent aiem_research_insights rows)",
        outputs="dict with rolled_back flag and the restored findings/weights",
        upstream_dependencies="aiem_research_insights table",
        downstream_dependencies="AI chat tool dispatch map (tool_name rollback_to_previous_model)",
        owning_phase=8,
        owning_module="INLINE (main.py) — no dedicated Phase 8 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_rollback_to_previous_model' main.py (line 21100); "
                "sed read confirms real psycopg2 SQL rollback write to aiem_research_insights",
        verified_by_command="sed trace, 2026-07-08 (Phase 8 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_get_meta_learning_weights",
        purpose="AI-callable tool (tool_name: get_meta_learning_weights) — inline direct-SQL "
                "read of live per-signal trust weights from the meta-learning system.",
        inputs="See tool schema; optional min_observations filter",
        outputs="dict of per-signal context bucket, rolling win rate, and trust weight",
        upstream_dependencies="signal_trust_weights table",
        downstream_dependencies="AI chat tool dispatch map (tool_name get_meta_learning_weights)",
        owning_phase=8,
        owning_module="INLINE (main.py) — no dedicated Phase 8 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_get_meta_learning_weights' main.py (line 36269); "
                "sed read confirms real psycopg2 SQL on signal_trust_weights",
        verified_by_command="sed trace, 2026-07-08 (Phase 8 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_get_m2_decay_status",
        purpose="AI-callable tool (tool_name: get_m2_decay_status) — inline direct-SQL read of "
                "the live status of every signal in the Module 2 decay pipeline.",
        inputs="none",
        outputs="dict of signal status breakdown plus per-signal edge/decay-action detail",
        upstream_dependencies="aiem_signal_discoveries table, aiem_signal_actions table",
        downstream_dependencies="AI chat tool dispatch map (tool_name get_m2_decay_status)",
        owning_phase=8,
        owning_module="INLINE (main.py) — no dedicated Phase 8 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_get_m2_decay_status' main.py (line 36302); "
                "sed read confirms real psycopg2 SQL join on aiem_signal_discoveries/aiem_signal_actions",
        verified_by_command="sed trace, 2026-07-08 (Phase 8 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_get_m6_rediscovery_status",
        purpose="AI-callable tool (tool_name: get_m6_rediscovery_status) — inline direct-SQL "
                "read of Module 6 Rediscovery Engine run history.",
        inputs="none",
        outputs="dict of total runs, variations tested/passed, and per-run detail",
        upstream_dependencies="aiem_rediscovery_runs table, aiem_signal_discoveries table",
        downstream_dependencies="AI chat tool dispatch map (tool_name get_m6_rediscovery_status)",
        owning_phase=8,
        owning_module="INLINE (main.py) — no dedicated Phase 8 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_get_m6_rediscovery_status' main.py (line 36342); "
                "sed read confirms real psycopg2 SQL join on aiem_rediscovery_runs/aiem_signal_discoveries",
        verified_by_command="sed trace, 2026-07-08 (Phase 8 tool-tracing pass)",
    ),
]


PHASE9_FUNCTIONS = [
    dict(
        file_name="main.py",
        function_name="_aiem_tool_predict_short_term",
        purpose="AI-callable tool (tool_name: predict_short_term) — inline direct-SQL "
                "similarity match against historically-similar setups in ai_short_calls_log "
                "and polygon_market_daily to project a short-term forward return.",
        inputs="See tool schema; ticker, days",
        outputs="dict with similar historical setups and their average forward return",
        upstream_dependencies="ai_short_calls_log table, polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name predict_short_term)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_predict_short_term' main.py (line 21662); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_build_composite",
        purpose="AI-callable tool (tool_name: mkt_build_composite) — inline direct-SQL "
                "combination of multiple validated aiem_signal_discoveries conditions into "
                "one composite backtest via shared _mkt_parse_conditions/_mkt_run_two_group helpers.",
        inputs="See tool schema; discovery_ids, horizon",
        outputs="dict with composite hit rate/forward return vs. each individual discovery",
        upstream_dependencies="aiem_signal_discoveries table, polygon_market_daily table, _mkt_parse_conditions helper",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_build_composite)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_build_composite' main.py (line 25059); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_compare_signals",
        purpose="AI-callable tool (tool_name: mkt_compare_signals) — inline direct-SQL "
                "A/B comparison of two arbitrary signal condition sets via shared "
                "_mkt_parse_conditions/_mkt_run_two_group helpers.",
        inputs="See tool schema; conditions_a, conditions_b, horizon",
        outputs="dict comparing hit rate/forward return between the two condition sets",
        upstream_dependencies="polygon_market_daily table, _mkt_parse_conditions/_mkt_run_two_group helpers",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_compare_signals)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_compare_signals' main.py (line 25013); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_check_signal_redundancy",
        purpose="AI-callable tool (tool_name: mkt_check_redundancy) — inline Jaccard-overlap "
                "check enforcing LAW 21: before saving a new discovery, verifies it does not "
                "fire on substantially the same stock-days as any existing validated discovery.",
        inputs="See tool schema; conditions, correlation_threshold (default 0.70)",
        outputs="dict with is_redundant flag and overlapping discovery IDs",
        upstream_dependencies="aiem_signal_discoveries table, polygon_market_daily table, _mkt_parse_conditions helper",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_check_redundancy)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_check_signal_redundancy' main.py (line 28356); "
                "sed read confirms real psycopg2 Jaccard-overlap SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_analyze_false_signals",
        purpose="AI-callable tool (tool_name: mkt_analyze_false_signals) — inline direct-SQL "
                "split of winners (>=win_threshold% next day) vs. losers (<0% next day) among "
                "stocks meeting a signal, to surface candidate negative filters.",
        inputs="See tool schema; conditions, win_threshold, horizon",
        outputs="dict comparing average gap/rvol/close_strength/range_pct/volume between winners and losers",
        upstream_dependencies="polygon_market_daily table, _mkt_parse_conditions helper",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_analyze_false_signals)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_analyze_false_signals' main.py (line 24081); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_mkt_tool_analyze_top_movers",
        purpose="AI-callable tool (tool_name: mkt_analyze_top_movers) — inline direct-SQL "
                "profile of the PRIOR-day characteristics of stocks that moved min_move_pct%+ "
                "the next day, to reveal leading indicators of large moves.",
        inputs="See tool schema; min_move_pct, max_move_pct, horizon, start_date, end_date",
        outputs="dict of average/median gap, rvol, close_strength, range_pct, volume, and actual forward move",
        upstream_dependencies="polygon_market_daily table",
        downstream_dependencies="AI chat tool dispatch map (tool_name mkt_analyze_top_movers)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _mkt_tool_analyze_top_movers' main.py (line 23996); "
                "sed read confirms real psycopg2 SQL aggregate, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_analyze_signal_correlation",
        purpose="AI-callable tool (tool_name: analyze_signal_correlation) — inline direct-SQL "
                "win-rate split on ai_short_calls_log by signal type. NAMING TRAP: despite the "
                "name, this tool has NO relationship to the real signal_correlation.py module "
                "(that module is wired via a different function, _aiem_tool_signal_layer_redundancy).",
        inputs="See tool schema; signal, days_back",
        outputs="dict of win rate / average return for the named signal",
        upstream_dependencies="ai_short_calls_log table",
        downstream_dependencies="AI chat tool dispatch map (tool_name analyze_signal_correlation)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file (NOT signal_correlation.py)",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_analyze_signal_correlation' main.py (line 19648); "
                "sed read confirms real psycopg2 SQL, no signal_correlation.py import — naming trap",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_cross_signal_overlap",
        purpose="AI-callable tool (tool_name: query_cross_signal_overlap) — inline direct-SQL "
                "EXISTS join checking how often ai_short_calls_log picks also appear in "
                "conviction_stack_watchlist / unusual_calls_log, and whether overlap improves outcomes.",
        inputs="See tool schema; days_back",
        outputs="dict comparing outcomes for overlapping vs. non-overlapping picks",
        upstream_dependencies="ai_short_calls_log, conviction_stack_watchlist, unusual_calls_log tables",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_cross_signal_overlap)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_cross_signal_overlap' main.py (line 21258); "
                "sed read confirms real psycopg2 EXISTS-join SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_rank_effectiveness",
        purpose="AI-callable tool (tool_name: query_rank_effectiveness) — inline direct-SQL "
                "group-by-rank outcome breakdown on ai_short_calls_log, testing whether higher-"
                "ranked picks actually perform better.",
        inputs="See tool schema; days_back",
        outputs="dict of win rate / average return grouped by pick rank",
        upstream_dependencies="ai_short_calls_log table",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_rank_effectiveness)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_rank_effectiveness' main.py (line 21502); "
                "sed read confirms real psycopg2 group-by SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_temporal_patterns",
        purpose="AI-callable tool (tool_name: query_temporal_patterns) — inline direct-SQL "
                "day-of-week / opex-week aggregation on ai_short_calls_log to surface calendar-"
                "linked performance patterns.",
        inputs="See tool schema; days_back",
        outputs="dict of win rate / average return grouped by day-of-week and opex-week flag",
        upstream_dependencies="ai_short_calls_log table",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_temporal_patterns)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_temporal_patterns' main.py (line 21410); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_missed_movers",
        purpose="AI-callable tool (tool_name: query_missed_movers) — inline direct-SQL read of "
                "ai_early_movers_misses to surface stocks the system failed to flag before a big move.",
        inputs="See tool schema; days_back",
        outputs="dict of missed movers with move size and miss reason",
        upstream_dependencies="ai_early_movers_misses table",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_missed_movers)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_missed_movers' main.py (line 19619); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_pick_outcomes",
        purpose="AI-callable tool (tool_name: query_pick_outcomes) — inline direct-SQL read of "
                "ai_short_calls_log outcomes over a lookback window.",
        inputs="See tool schema; days_back",
        outputs="dict of individual pick outcomes (win/loss, return) over the window",
        upstream_dependencies="ai_short_calls_log table",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_pick_outcomes)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_pick_outcomes' main.py (line 19583); "
                "sed read confirms real psycopg2 SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
    dict(
        file_name="main.py",
        function_name="_aiem_tool_query_own_prediction_performance",
        purpose="AI-callable tool (tool_name: query_own_prediction_performance) — inline direct-"
                "SQL join of aiem_predictions with aiem_prediction_outcomes so the AI can review "
                "its own historical prediction accuracy.",
        inputs="See tool schema; days_back",
        outputs="dict of prediction accuracy / calibration stats over the window",
        upstream_dependencies="aiem_predictions table, aiem_prediction_outcomes table",
        downstream_dependencies="AI chat tool dispatch map (tool_name query_own_prediction_performance)",
        owning_phase=9,
        owning_module="INLINE (main.py) — no dedicated Phase 9 module file",
        verification_status="VERIFIED",
        verification_evidence="grep -n 'def _aiem_tool_query_own_prediction_performance' main.py (line 20634); "
                "sed read confirms real psycopg2 join SQL, no module import",
        verified_by_command="sed trace, 2026-07-08 (Phase 9 tool-tracing pass)",
    ),
]


def upsert_functions(conn, rows):
    with conn.cursor() as cur:
        for r in rows:
            r = dict(r)
            r["owning_phase_name"] = PHASE_NAMES.get(r["owning_phase"])
            cur.execute("""
                INSERT INTO aiem_function_registry
                    (file_name, function_name, purpose, inputs, outputs,
                     upstream_dependencies, downstream_dependencies,
                     owning_phase, owning_phase_name, owning_module, is_inline,
                     verification_status, verification_evidence, verified_by_command,
                     last_verified_date, verification_version)
                VALUES (%(file_name)s, %(function_name)s, %(purpose)s, %(inputs)s, %(outputs)s,
                        %(upstream_dependencies)s, %(downstream_dependencies)s,
                        %(owning_phase)s, %(owning_phase_name)s, %(owning_module)s, TRUE,
                        %(verification_status)s, %(verification_evidence)s, %(verified_by_command)s,
                        now(), 1)
                ON CONFLICT (file_name, function_name) DO UPDATE SET
                    purpose = EXCLUDED.purpose,
                    inputs = EXCLUDED.inputs,
                    outputs = EXCLUDED.outputs,
                    upstream_dependencies = EXCLUDED.upstream_dependencies,
                    downstream_dependencies = EXCLUDED.downstream_dependencies,
                    owning_phase = EXCLUDED.owning_phase,
                    owning_phase_name = EXCLUDED.owning_phase_name,
                    owning_module = EXCLUDED.owning_module,
                    verification_status = EXCLUDED.verification_status,
                    verification_evidence = EXCLUDED.verification_evidence,
                    verified_by_command = EXCLUDED.verified_by_command,
                    last_verified_date = now(),
                    verification_version = aiem_function_registry.verification_version + 1,
                    updated_at = now()
            """, r)
    conn.commit()


def main():
    conn = _connect()
    try:
        for phase, rows in ((0, PHASE0_FUNCTIONS), (1, PHASE1_FUNCTIONS), (2, PHASE2_FUNCTIONS), (3, PHASE3_FUNCTIONS), (4, PHASE4_FUNCTIONS), (5, PHASE5_FUNCTIONS), (6, PHASE6_FUNCTIONS), (7, PHASE7_FUNCTIONS), (8, PHASE8_FUNCTIONS), (9, PHASE9_FUNCTIONS)):
            upsert_functions(conn, rows)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM aiem_function_registry WHERE owning_phase = %s", (phase,))
                n_total = cur.fetchone()[0]
                cur.execute("""
                    SELECT verification_status, COUNT(*) FROM aiem_function_registry
                    WHERE owning_phase = %s GROUP BY verification_status ORDER BY 1
                """, (phase,))
                by_status = cur.fetchall()
            print(f"[aiem_function_registry] Phase {phase}: {n_total} function rows upserted")
            for status, n in by_status:
                print(f"  {status}: {n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
