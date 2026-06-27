# FOR CLAUDE — StockScanner AI: Quant Agent Chat Tab

## What I need from you

I'm building a **Quant Agent chat tab** inside my StockScanner dashboard.
The backend AIEM engine and DB are already built. The frontend chat UI is built.
I need you to review everything below and give me any improvements or fixes for:

1. The 3 Flask API endpoints that handle chat (already coded — review + improve)
2. The React frontend `QuantAgentTab` component (already coded — review + improve)
3. Any gaps you see between what the AIEM can actually do vs what the UI exposes

---

## Architecture Overview

- **Backend**: Flask (Python), single file `main.py` (~41,600 lines)
- **Frontend**: React + TypeScript, Vite, `Dashboard.tsx` (~15,800 lines)
- **DB**: PostgreSQL
- **AI**: OpenAI `gpt-4o` via Replit AI proxy
- **URL prefix**: Flask routes all start with `/stock-api/`, Vite dev on port ~21411

---

## Part 1 — The AIEM Session Runner (core reasoning engine)

This is the function that actually runs the AI research. It's called by the chat endpoints.

```python
def _run_aiem_focused_session(session_name: str, focus_prompt: str,
                               max_iterations: int = 12):
    """
    Parameterized research session. The agent uses all its tools but starts
    with a specific focus question. Returns the agent's final text response.
    """
    import json as _fsj
    from openai import OpenAI as _OAIFS
    _oai = _OAIFS(
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    )

    # Full tool map — 88 tools available to the agent
    _fs_tool_map = {
        # Market research tools
        "mkt_behavioral_templates":    _mkt_behavioral_templates,
        "mkt_find_behavioral_matches": _mkt_find_behavioral_matches,
        "mkt_retrospective_backtest":  _mkt_retrospective_backtest,
        "mkt_ticker_deep_compare":     _mkt_ticker_deep_compare,
        "mkt_net_flow_db":             _mkt_net_flow_db,
        "mkt_ticker_options_history":  _mkt_ticker_options_history,
        "mkt_options_flow_scan":       _mkt_options_flow_scan,
        "mkt_options_predicts_price":  _mkt_options_predicts_price,
        "mkt_cross_confirm_options":   _mkt_cross_confirm_options_price,
        "mkt_explore_dimensions":      _mkt_tool_explore_dimensions,
        "mkt_test_signal":             _mkt_tool_test_signal,
        "mkt_analyze_top_movers":      _mkt_tool_analyze_top_movers,
        "mkt_find_thresholds":         _mkt_tool_find_thresholds,
        "mkt_validate_oos":            _mkt_tool_validate_oos,
        "mkt_save_discovery":          _mkt_tool_save_discovery,
        "mkt_load_discoveries":        _mkt_tool_load_discoveries,
        "mkt_factor_correlations":     _mkt_tool_factor_correlations,
        "mkt_quiet_accumulation":      _mkt_tool_quiet_accumulation,
        "mkt_pre_squeeze_warning":     _mkt_tool_pre_squeeze_warning,
        "mkt_52week_momentum":         _mkt_52week_high_momentum,
        "mkt_compute_indicators":      _mkt_compute_indicators,
        "mkt_screen_by_indicator":     _mkt_screen_by_indicator,
        "mkt_layer9_score":            _mkt_layer9_score,
        "mkt_get_stock_history":       _mkt_get_stock_history,
        "mkt_screen_period":           _mkt_screen_period,
        "mkt_historical_study":        _mkt_historical_study,
        "mkt_compute_momentum":        _mkt_tool_compute_momentum,
        "mkt_build_composite":         _mkt_tool_build_composite,
        "mkt_segment_by_cap_tier":     _mkt_tool_segment_by_cap_tier,
        "mkt_regime_filter":           _mkt_tool_regime_filter,
        "mkt_volume_patterns":         _mkt_tool_volume_patterns,
        "mkt_price_patterns":          _mkt_tool_price_patterns,
        "mkt_capitulation_detector":   _detect_capitulation_signature,
        # Signal analysis tools
        "analyze_missed_movers":       _aiem_tool_analyze_missed_movers,
        "query_pick_outcomes":         _aiem_tool_query_pick_outcomes,
        "test_new_signal":             _aiem_tool_test_new_signal,
        "run_statistical_significance": _aiem_tool_run_statistical_significance,
        "save_research_model":         _aiem_tool_save_research_model,
        "search_past_findings":        _aiem_tool_search_past_findings,
        "list_signal_dimensions":      _aiem_tool_list_signal_dimensions,
        # Scanner + email tools
        "send_discovery_alert":        _aiem_tool_send_discovery_alert,
        "breakout_discover":           _aiem_tool_breakout_discover,
        "gap_continuation_score":      _aiem_tool_gap_continuation_score,
        "squeeze_subscore":            _aiem_tool_squeeze_subscore,
        "run_risk_gate":               _aiem_tool_run_risk_gate,
        "divergence_scan":             _aiem_tool_divergence_scan,
        "check_price_bullish":         _aiem_tool_check_price_bullish,
        "regime_overlay_check":        _aiem_tool_regime_overlay_check,
        # VWAP tools
        "vwap_compute_features":       _aiem_tool_vwap_compute_features,
        "vwap_price_vs":               _aiem_tool_vwap_price_vs,
        "vwap_reclaim_detect":         _aiem_tool_vwap_reclaim_detect,
        # Meta-learning trust tools
        "trust_classify_context":      _aiem_tool_trust_classify_context,
        "trust_update":                _aiem_tool_trust_update,
        "trust_get_weights":           _aiem_tool_trust_get_weights,
        "trust_apply_to_candidates":   _aiem_tool_trust_apply_to_candidates,
        # ... (88 tools total)
    }

    _fs_schema = _AIEM_AGENT_TOOLS  # OpenAI function-calling schema for all tools

    session_system = (
        _AIEM_AGENT_SYSTEM +  # ~2,000 word system prompt (see Part 2)
        f"\n\nSESSION FOCUS ({session_name}): {focus_prompt}\n"
        "Start immediately with the most relevant tools for this session's focus."
    )

    messages = [
        {"role": "system", "content": session_system},
        {"role": "user", "content": focus_prompt}
    ]

    _last_text = ""
    for _i in range(max_iterations):
        resp = _oai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=_fs_schema,
            tool_choice="auto",
            max_tokens=2000,
            temperature=0.3,
        )
        msg = resp.choices[0].message
        messages.append(msg)
        if msg.content:
            _last_text = msg.content
        if not msg.tool_calls:
            break
        for tc in msg.tool_calls:
            fn = tc.function.name
            args = json.loads(tc.function.arguments)
            result = _fs_tool_map[fn](**args) if fn in _fs_tool_map else {"error": f"unknown: {fn}"}
            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(result, default=str)[:6000]
            })

    return _last_text  # Returns agent's final text answer
```

---

## Part 2 — AIEM System Prompt (summary)

The agent is told it has:
- 495 days of historical stock data (polygon_market_daily, 12K stocks/day)
- Full options flow history (call sweeps, OI buildup, unusual activity)
- Its own pick history with graded outcomes (T+1/T+3/T+7)
- 88 research tools spanning: backtesting, signal discovery, options analysis, VWAP, regime filtering, statistical significance testing, ML retraining

The agent is instructed to: use tools first, answer last; always run OOS validation before saving; never fabricate numbers; use the DB not memory.

---

## Part 3 — The 3 Chat API Endpoints (already coded in main.py)

```python
# POST /stock-api/aiem/chat
# Body: {"question": "..."}
# Returns: {"job_id": "uuid", "status": "pending"}
# Saves to DB, spawns daemon thread, returns immediately

@app.route("/stock-api/aiem/chat", methods=["POST"])
def aiem_chat_start():
    data     = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()[:800]
    if not question:
        return jsonify({"error": "question is required"}), 400

    job_id = str(uuid.uuid4())
    # INSERT INTO quant_agent_sessions (job_id, question, status) VALUES (...)

    prompt = (
        f"The user asks: '{question}'\n\n"
        f"Research this thoroughly using your tools. "
        f"If they mention specific tickers, use mkt_retrospective_backtest. "
        f"If they ask about a pattern or signal, use mkt_test_signal to validate with real data. "
        f"End with a clear, direct answer. Be concise but complete — 3-5 paragraphs max."
    )

    def _worker():
        # UPDATE status='running'
        result = _run_aiem_focused_session(
            session_name=f"quant_chat_{job_id[:8]}",
            focus_prompt=prompt,
            max_iterations=3,   # ~2-4 min per session
        )
        # UPDATE status='done', answer=result
        # or UPDATE status='error', error=...

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "pending"})


# GET /stock-api/aiem/chat/<job_id>
# Returns: {"job_id", "status", "answer", "error", "created_at"}
# status: pending → running → done | error

@app.route("/stock-api/aiem/chat/<job_id>", methods=["GET"])
def aiem_chat_poll(job_id):
    # SELECT status, answer, error, created_at FROM quant_agent_sessions WHERE job_id=...
    return jsonify({"job_id": job_id, "status": status, "answer": answer, ...})


# GET /stock-api/aiem/chat/history
# Returns: last 20 sessions as array

@app.route("/stock-api/aiem/chat/history", methods=["GET"])
def aiem_chat_history():
    # SELECT job_id, question, status, answer, created_at FROM quant_agent_sessions ORDER BY created_at DESC LIMIT 20
    return jsonify([...])
```

---

## Part 4 — Database Table

```sql
CREATE TABLE quant_agent_sessions (
    job_id     TEXT PRIMARY KEY,
    question   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | error
    answer     TEXT,
    error      TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Part 5 — Frontend QuantAgentTab (already coded in Dashboard.tsx)

The component is already wired in as the last tab (`🤖 QUANT AGENT`). It:
- On mount: `GET /stock-api/aiem/chat/history` → shows last 20 sessions as messages
- On submit: `POST /stock-api/aiem/chat` → gets job_id → polls `GET /stock-api/aiem/chat/{job_id}` every 3s
- Shows animated dots + elapsed timer while `status === "running"`
- Shows answer as pre-formatted text when `status === "done"`
- 5 example question chips shown when history is empty
- Enter to send, Shift+Enter for newline
- Bloomberg dark theme (`#060c14` background, blue/green accents)

The component uses `useState`, `useEffect`, `useRef` from React.
It uses `import.meta.env.BASE_URL` for API path prefix.

---

## Part 6 — Key DB Tables the AIEM Can Query

| Table | Rows (approx) | What it contains |
|---|---|---|
| `polygon_market_daily` | 427,000+ | 12K stocks × every trading day since Apr 2026. Columns: ticker, date, open, high, low, close, volume, rvol, gap_pct, close_strength, score, sector |
| `unusual_calls_log` | 15,000+ | Detected unusual call sweeps: ticker, date, strike, expiry, volume, openInterest, vol_oi_ratio, premium, conviction |
| `call_sweep_log` | 8,000+ | Raw call sweep events from Polygon/Tradier |
| `oi_daily_snapshot` | 25,000+ | Daily OI per ticker/strike/expiry |
| `eod_outcomes` | 10,000+ | Price outcomes: open_to_close_pct, open_to_high_pct, fade_risk_signal per ticker/date |
| `ai_early_movers_log` | 2,000+ | AI-picked stocks with T+1/T+3/T+7 outcome grades |
| `aiem_signal_discoveries` | 50+ | Validated signals from prior AIEM research sessions |
| `conviction_stack_watchlist` | daily | Current multi-layer conviction scores (8 layers) |
| `polygon_rvol_scan` | 100K+ | Daily RVOL + gap data from Polygon full-market scan |
| `scan_history` | 50,000+ | Morning scan picks with standout score, flow ratio, rel_vol |
| `quant_agent_sessions` | new | This chat tab's session history |

---

## Part 7 — What Works, What's Missing

**Already working:**
- Backend endpoints respond correctly (tested)
- Frontend component renders and polls
- History loads on mount
- Elapsed timer ticks during research
- NaN/Inf scrubbed from all JSON

**Potential improvements Claude should suggest:**
1. Should the prompt be more specific about which tools to call for different question types?
2. Is `max_iterations=3` enough or should it be dynamic based on question complexity?
3. Should the frontend markdown-render the answer (the agent outputs markdown-ish text)?
4. Should we add streaming (SSE) instead of polling for better UX?
5. Should we rate-limit to 1 concurrent session (AIEM is CPU/API intensive)?
6. Should the history show which tools were called during each session?

---

## Request

Please review all of the above and give me:
1. Any bugs or improvements to the 3 Flask endpoints
2. Any improvements to the React `QuantAgentTab` component
3. Specific fixes for the 6 potential improvement areas above
4. Any other gaps you see

Give me the actual code — not pseudocode. I'll paste it directly into Replit.
