---
name: AIEM direction-agnostic architecture
description: CALL/PUT/SHORT paper trading wiring, direction-aware P&L in MTM, always-show-both-strikes system prompt rule
---

## Rule
AIEM is fully direction-agnostic: it picks CALL_OPTION, PUT_OPTION, or SHORT_STOCK based purely on R:R. For every ticker it analyzes it MUST present both a CALL and PUT setup with specific strike+expiry before choosing one.

## Why
User requirement: never default bullish. A PUT with higher conviction beats a CALL with lower conviction.

## How to apply

### New module
`aiem_options_intel.py` — 4 functions wired as tools:
- `compute_expected_move(ticker)` → ±EM range used to derive strikes
- `compute_iv_rank_live(ticker)` → IV rank (prefer buying when < 50)
- `compute_oi_by_strike(ticker)` → highest-OI call/put strikes = price targets
- `compute_bearish_signals(ticker)` → FEAR_PREMIUM, LONG_GAMMA, distribution

### DB schema
`aiem_paper_trades.direction TEXT NOT NULL DEFAULT 'BULLISH'` — added via ALTER TABLE in `_init_aiem_paper_trades_table`.

### Pick candidates (bearish sources)
- Source #12: FEAR_PREMIUM + LONG_GAMMA → PUT_OPTION, direction="BEARISH", macro-gated (_macro_bias != 1)
- Source #13: gap_down_distribution → SHORT_STOCK, direction="BEARISH", macro-gated

### Direction-aware P&L (applied in 3 places)
All three locations use this same logic:
```
CALL_OPTION: move_pct = (last - entry) / entry * 100;  pnl_pct = max(-100, move_pct * 2.0)
PUT_OPTION:  move_pct = (entry - last) / entry * 100;  pnl_pct = max(-100, move_pct * 2.0)  ← INVERTED 2x
SHORT_STOCK: pnl_pct  = (entry - last) / entry * 100                                          ← INVERTED 1x
STOCK/ETF:   pnl_pct  = (last - entry) / entry * 100
```
Locations updated:
1. `_aiem_paper_mark_to_market` first for-loop (position snapshot build)
2. `_aiem_paper_mark_to_market` second for-loop (apply decisions, step 5)
3. `_aiem_close_paper_trade_and_run_loop` mode="close" branch

### _price_map now 7-tuple
`_price_map[_id] = (_last, _entry_f, _qty_f, _not_f, _ttype, _trade_date, _dir)`
Second for-loop unpacks: `_last, _entry_f, _qty_f, _not_f, _, _, _dir2 = _price_map[_id]`

### System prompt DIRECTION RULE
AIEM must always call `mkt_expected_move` + `mkt_oi_by_strike` per ticker and present:
- CALL setup: spot + EM → nearest $0.50 strike, nearest weekly 7-14 days out
- PUT setup:  spot − EM → nearest $0.50 strike, nearest weekly 7-14 days out
Then pick the one with better R:R (IV rank < 50 = prefer buying; GEX + skew alignment; macro bias).

### Telegram
Entry line shows `↓BEARISH` tag for non-BULLISH trades.
