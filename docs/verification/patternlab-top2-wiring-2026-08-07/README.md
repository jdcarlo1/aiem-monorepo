# Directive_PatternLabTop2_WiringVerification_2026-08-07

Evidence for PR #45 (narrow-wing butterfly + bullish risk reversal paper wiring).

## Raw artifacts

| File | Contents |
|------|----------|
| `VERIFY_CONSOLE.txt` | Full verifier console (`PASS_COUNT=35 FAIL_COUNT=1`) |
| `GIT_DIFF_5_FILES.patch` | `git diff` of the 5 production files vs `main` |
| `CODE_GREP_GATES.txt` | Gate / builder / persist greps |
| `CATALOG_BT_EXCERPT.txt` | Catalog BT rules + builders from PR #44 branch |
| `DB_QUERY_RAW.txt` | `aiem_paper_trades` rows for both strategies |
| `LIVE_SNAPSHOT_RAW.json` / `LIVE_SNAPSHOT_PRETTY.json` | Live `nclexai.org` snapshot |

Runner: `artifacts/stock-scanner-api/verify_pattern_lab_top2_wiring.py`

## Verdicts (no narrative padding)

### 1. Spec match

| Rule | Narrow-Wing | Bullish RR | Proof |
|------|-------------|------------|-------|
| Structure | ATM ±2 calls | call k+5 / put k−5 | `PASS narrow_atm_pm2` / `PASS rr_call_kp5_put_km5` |
| Debit ≤$500 | `SKIP_BUDGET` on $650 | n/a (credit) | `injected_unit_cost_usd=650.0` → `SKIP_BUDGET` |
| Cash-secured | n/a | enforced | `$10k` free → `SKIP_COLLATERAL need $49500`; `$100k` → enters + reserves `$49500` |
| TP | +200% of \|entry\| | +75% of \|entry\| | ledger constants + TP math PASS |
| Monday 09:30 | `ENTRY_AFTER='09:30'` + weekday==0 | same | PASS entry gates |
| ~3wk Friday | `weeks_ahead=3` | same | PASS |
| Friday 15:30 flatten | `FLATTEN_TIME='15:30'` | same | PASS |
| No stop | `stop_loss: null` | same | PASS |
| $100k RR book | — | `RR_PAPER_CAPITAL_USD=100000.0` | PASS |

### 2. BT parity

Matches catalog BT on: risk $500, Polygon daily pricing (not Tradier), wing/strike builders, TP 200/75, no stop, `weeks_ahead=3`.

Documented differences (not papered over):
- Paper entry clock = Monday **09:30**; catalog BT = Monday **daily asof** (no intraday clock).
- Paper flatten = **15:30** clock; catalog BT = last daily bar `EXPIRY_FLATTEN`.
- Paper **adds** CSP `SKIP_COLLATERAL`; catalog BT does **not** model cash-secured collateral.

### 3. DB write

Produced on agent `DATABASE_URL` → **local `asym_dry@127.0.0.1`** (same schema as prior asym dry-run):

- id=4 `narrow_wing_butterfly` CLOSED notional=185 pnl=370 pnl_pct=200 strike=498
- id=5 `bullish_risk_reversal` CLOSED notional=-150 pnl=112.50 pnl_pct=75 strike=505

**CANNOT PRODUCE** rows against production Neon: agent DSN is local-only (`127.0.0.1`), not Neon.

Also fixed credit `pnl_pct` to use `ABS(notional)` (was signing negative when notional < 0).

### 4. Dashboard / live snapshot

**CANNOT PRODUCE** live top-2 cards.

`https://nclexai.org/stock-api/pattern-lab/snapshot` keys = `['f3','gap_fill','orb']` only — PR #45 not merged/deployed; stock-api not restarted. Prior asym keys from PR #42 also absent on live.

Local-on-branch engine snapshot **does** expose both keys with correct rules (see VERIFY section 9).

### 5. Summary counts

```
PASS_COUNT=35
FAIL_COUNT=1   # live_snapshot_top2 only
```

## Follow-up 2026-08-07

See:
- `FAIL_NAMED.txt` — the one FAIL is `live_snapshot_top2` (undeployed)
- `CLOCK_COMPARE_*.txt/json` + `CLOCK_RECONCILE_DECISION.md` — PAPER_0930 diverges; paper now `require_exact=True`
- `DEPLOY_AND_PROD_DB_BLOCKERS.txt` — merge/restart + Neon secret required
