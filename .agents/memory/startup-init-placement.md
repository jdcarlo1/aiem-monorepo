---
name: Startup init placement rule
description: Where to insert new module schema init blocks in main.py without causing SyntaxError
---

## Rule
New `try/except` schema init blocks must be placed as **sibling blocks** after the outer `[aiem_integrity]` try/except — never inserted inside it.

## Why
The outer `[aiem_integrity]` try block wraps all core integrity module inits and has its own `except Exception as _e_aiem_init` clause. Inserting a new `try` inside that outer try body and then an `except` for it means Python sees the outer try as having two `except` clauses (the inner except + the outer except), which raises:
`SyntaxError: expected 'except' or 'finally' block`

## How to apply
Anchor text to match for insertion point:
```
    print("[aiem_integrity] pre_decision_risk_gate schema ready")
except Exception as _e_aiem_init:
    print(f"[aiem_integrity] schema init error: {_e_aiem_init}")
```
Place the new block AFTER this closing except:
```python
try:
    import new_module as _nm
    _nm.init_schema()
    print("[new_module] schema ready")
except Exception as _e:
    print(f"[new_module] schema init error: {_e}")
```

Modules WITHOUT a schema (no startup init needed):
portfolio_allocator, causal_discovery, ensemble_combiner,
execution_simulator, market_regime_overlay,
smart_money_divergence_detector, breakout_signature_discovery,
premarket_gap_continuation_scanner
