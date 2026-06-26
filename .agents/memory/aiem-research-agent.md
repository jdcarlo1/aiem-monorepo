---
name: AI Research Agent
description: Autonomous AI agent that queries its own pick/miss history, discovers signal correlations, backtests scoring models, and self-improves weekly via OpenAI function-calling loop.
---

# Architecture

- `_run_aiem_research_agent(max_iterations=15)` — gpt-4o function-calling loop in `main.py`
- Agent autonomously decides what to investigate; no hardcoded analysis path
- Runs as daemon thread (never blocks HTTP); triggered Sunday 8 PM ET + manual admin endpoint

# 7 Tools the agent can call

| Tool | Purpose |
|------|---------|
| `query_pick_outcomes` | Full T+3/T+7 history from `ai_early_movers_log` |
| `query_missed_movers` | Missed 5%+ movers from `ai_early_movers_misses` |
| `analyze_signal_correlation` | Win rate for picks WITH vs WITHOUT a signal |
| `compare_picks_vs_misses` | Side-by-side bias analysis |
| `discover_numeric_patterns` | Quartile analysis on day_ret/vol_oi/price |
| `test_scoring_hypothesis` | Backtest proposed weights on settled picks |
| `save_research_model` | Persist findings → `aiem_research_insights` |

# Persistence

- DB table: `aiem_research_insights` (research_date UNIQUE, findings TEXT, scoring_adjustments JSONB, confidence, tool_calls_made)
- `_get_aiem_research_context()` reads latest model and injects it into every daily pick prompt via `_get_aiem_feedback()`

# Endpoints

- `POST /stock-api/admin/run-aiem-research` — manual trigger (X-Admin-Token required); returns immediately, agent runs in background
- `GET /stock-api/aiem-research-status` — view research history and current model

# Confidence progression

**Why:** T+3/T+7 outcomes take 3-7 trading days to settle. On first run, `t3_win IS NOT NULL` rows will be 0 → agent saves LOW confidence with default weights. After 2-4 weeks of picks, it reaches MEDIUM/HIGH and starts finding real patterns.

**How to apply:** Don't panic if first few runs show LOW confidence. Check again after 30 days of accumulated picks.

# Important: `\n` in string literals inside heredoc scripts

When inserting Python code via a `python3 - << 'PYEOF'` heredoc, `\n` inside string literals gets interpreted as real newlines → syntax error. Fix: use `"\\n"` (double-escape) or use string concatenation with `+ "\n" +` instead of f-string multi-line literals.
