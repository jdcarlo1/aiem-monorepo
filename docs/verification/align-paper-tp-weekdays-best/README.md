# Align paper TPs to weekdays 2y no-stop bests

Paper books (AIM Pattern Lab + OE Strategies share `build_default_asym_ledgers`)
must use the same fixed TP% as the archived weekdays ranking:

| Strategy | Paper TP | Mode |
|----------|----------|------|
| narrow_wing_butterfly | 300% | fixed_pct |
| put_butterfly | 275% | fixed_pct |
| call_butterfly | 275% | fixed_pct |
| put_ladder | 300% | fixed_pct |
| call_condor | 300% | fixed_pct |
| put_condor | 300% | fixed_pct |

Sources: `docs/verification/spy-top6-sl-compare-weekdays/`, narrow-wing weekdays grid.

Condor dynamic plateau TP (`DYNAMIC_PLATEAU_TP_STRATEGIES`) is **disabled** so
live paper matches BT identical fixed 300%. Note: with rich debits, +300% can be
unreachable vs the $500 wing plateau — intentional for BT parity.

## Prove locally

```bash
python3 artifacts/stock-scanner-api/prove_weekdays_best_tps.py
python3 artifacts/stock-scanner-api/smoke_narrow_fly_rr_paper.py
```

Expect `ALL_OK=True` and narrow-wing TP 300.

## Live after deploy

After merge → auto-sync `main`→`dev` → Replit `git pull --ff-only origin dev` → Publish:

```bash
curl -sS 'https://nclexai.org/stock-api/pattern-lab/snapshot' \
  | python3 -c "import sys,json; d=json.load(sys.stdin);
for k in ['narrow_wing_butterfly','put_butterfly','call_butterfly','put_ladder','call_condor','put_condor']:
 r=(d.get(k) or {}).get('rules') or {};
 print(k, r.get('take_profit_pct'), r.get('take_profit_mode'))"
```
