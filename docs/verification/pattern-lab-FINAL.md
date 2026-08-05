# Pattern Lab — Terminal Integration FINAL

**Directive:** `Directive_PatternLab_TerminalIntegration_2026-08-05`  
**Date:** 2026-08-05  
**Branch:** `cursor/pattern-lab-terminal-f9ec`  
**Scope:** Isolated Gap Fill + ORB paper ledgers in AIEM Institutional Terminal. No D1/D2/D3 changes.

---

## Deliverables

| Step | Status | Location |
|------|--------|----------|
| 1 Backend module as-is | DONE | `artifacts/stock-scanner-api/aim_paper_trading_engine.py` |
| 2 Wire into live bar loop | DONE | `main.py` `_run_td_intraday_capture` → `_pattern_lab_feed_from_spy_df` |
| 3 GET snapshot endpoint | DONE | `/pattern-lab/snapshot` + `/stock-api/pattern-lab/snapshot` |
| 4 Frontend panel | DONE | `artifacts/aiem-dashboard/src/pages/PatternLab.tsx` + Sidebar/App routes |

---

## sha256 before / after

| File | Before | After |
|------|--------|-------|
| `aim_paper_trading_engine.py` | ABSENT | `29ecd41784a69dcd93145258003056745b751513d935076b065ef9c4653f8a76` |
| `main.py` | `b47a2991f3f568c69a0b9170c8f55c3132973c17f35edec490638888a1ac6afc` | `aa6b51d532a83088ef49fa089f5a6e729bdfab98a28bcf3b82da421801092c91` |
| `PatternLab.tsx` | ABSENT | `2335e59c365deffc721f12eaee98f9b24627c763e57c7d816119c5fe43164e64` |
| `App.tsx` | `3d029387a3fd1dbd0fc5d546701fc7fcfd762e11606055f63fe7303cd2c01d5b` | `013e6b2b65bbaf803306ae6da6125d1b5303124cdd865d9d73a7eea7e6cddffc` |
| `Sidebar.tsx` | `03862c0d0225e0c4c1b0ca96ba4d32610419b5f8845954137d183f905d39a074` | `33035cfff7c38664e799a792e59f546a71df4fa390524608287cd566a89f5bec` |

---

## D1/D2/D3 untouched

```
git diff --stat -- aiem_master_orchestrator.py aiem_diagram2_stage_helpers.py \
  aiem_diagram3_governance.py diagram1_candidate_intake.py aiem_diagram2_trace_audit.py
(empty)
```

---

## Live snapshot (bar-driven)

Source: Neon `td_intraday_cache` SPY bars for **2026-08-05** (279 one-minute bars), prior_close **771.33** from `polygon_market_daily`. Engine replayed chronologically via `evaluate_market_bars`.

```
$ curl -sS http://127.0.0.1:5063/pattern-lab/snapshot
{
    "gap_fill": {
        "account_balance_usd": 10000.0,
        "active_position": null,
        "losses": 0,
        "net_liquidation_usd": 10000.0,
        "pattern": "GAP_FILL",
        "profit_rate_pct": 0.0,
        "total_trades": 0,
        "win_rate_pct": 0.0,
        "wins": 0
    },
    "orb": {
        "account_balance_usd": 10244.76,
        "active_position": {
            "entry": 770.64,
            "shares": 23,
            "side": "SHORT",
            "stop": 777.258425,
            "symbol": "SPY",
            "target": 758.2999999999998
        },
        "losses": 0,
        "net_liquidation_usd": 10220.38,
        "pattern": "ORB",
        "profit_rate_pct": 2.45,
        "total_trades": 1,
        "wins": 1,
        "win_rate_pct": 100.0
    }
}
```

**Bar-driven proof:** ORB balance ≠ $10,000 starting capital (`10244.76` after TARGET win); active SHORT still open. Gap Fill remains flat (no qualifying gap that day). Not a hardcoded mock.

Artifacts:
- `/opt/cursor/artifacts/pattern-lab-snapshot.json`
- `/opt/cursor/artifacts/pattern-lab-live-cards.html` (both cards rendered from live JSON)

---

## Wiring detail

- Singleton: `_get_pattern_lab_engine()` → `AIMPaperTradingEngine(symbol="SPY")`
- Feed: after SPY bars fetched in `_run_td_intraday_capture`, call `_pattern_lab_feed_from_spy_df`
- Column normalize: Tradier `Open/High/Low/Close` → engine `open/high/low/close`
- Prior close: latest `polygon_market_daily` SPY close before today ET
- Frontend: poll `/stock-api/pattern-lab/snapshot` every **30s** via `useApi` (same as Command Center / Decisions)
- Nav: Trading → Pattern Lab (`FlaskConical`), AppLayout unchanged

---

## Notes

- Endpoint registered as both `/pattern-lab/snapshot` (directive path) and `/stock-api/pattern-lab/snapshot` (terminal proxy prefix).
- Evidence Flask on `:5063` is agent-local for curl proof; production serves the same handlers from `main.py` after Publish.
