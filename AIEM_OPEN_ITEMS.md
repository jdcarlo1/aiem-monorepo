# AIEM Open Items — July 2 Gate-Integrity Audit

**Saved:** 2026-07-02  
**Source:** July 2 gate-integrity audit session  
**Standing rule:** Raw output only. No "PASS," "confirmed," or "working correctly" language
until the underlying data is shown. If evidence doesn't exist yet, say so and leave the row
as `hypothesis` — do not manufacture a pass.

---

## Question 1 — id 1's Gate 2 failure

**Status: ANSWERED — raw data below**

**Which gate is failing:**  
Gate 2 — win-rate floor. Code condition: `if float(signal_win_rate) < 54.0: return blocked`.

**Actual value vs threshold:**
```
signal_win_rate = 52.35
required        ≥ 54.00
delta           = −1.65pp
```

**Is this a data problem or a legitimate result?**  
Neither — it is a statistically significant negative OOS result, which is worse than "not yet
good enough." `aiem_discovery_outcomes` ran the signal against Jun 26–Jul 2 (6 identical runs):

```
retestable            = True
realized_n            = 905
realized_win_rate     = 46.96%   ← below 50% random baseline
realized_avg_ret      = −0.0154%
realized_p_value      = 0.0      ← statistically significant
predicted_vs_actual   = −5.39pp
checked_window        = 2026-06-26 → 2026-07-02
```

Gate 2's failure at discovery-time (52.35 < 54.0) is corroborated by the OOS outcome:
realized win rate 46.96% is below the 50% random baseline with n=905 and p=0.0. The signal
is not failing because the bar is set too high — the independent evidence goes the other
direction.

**id 1 current status remains `validated`.** Updating that status is a decision not made
here — reporting the raw data per the standing rule. The structured OOS evidence says the
signal is not performing at its discovery-time rate.

---

## Question 2 — id 4's negative backtest

**Status: ANSWERED — raw data below**

**Structured OOS (aiem_discovery_outcomes, all 5 runs identical):**
```
retestable            = True
realized_n            = 9
realized_win_rate     = 55.56%
realized_avg_ret      = −0.4383%
realized_p_value      = 0.3936   ← not significant
predicted_vs_actual   = −3.04pp
checked_window        = 2026-06-27 → 2026-07-02 (5 trading days post-discovery)
```

**Should id 4 be retired?**  
n=9 from a 5-trading-day post-discovery window is not sufficient to conclude either
direction. p=0.3936 is not significant. The in-sample result (58.6% WR, n=2224, p=0.0001)
is strong. Retiring on n=9 discards a well-supported in-sample signal based on a handful
of post-discovery trades. Left as `hypothesis` pending sufficient OOS accumulation.

**Is id 4 feeding any live alert, Telegram message, or paper trade?**  
No. Confirmed by code:
- `_mkt_tool_load_discoveries` (main.py ~L20968): `WHERE status = %s` with default
  `"validated"` — hypothesis rows excluded
- `_mkt_tool_signal_combination` (main.py ~L21544): `WHERE id IN (...) AND status = 'validated'`
- `_aiem_paper_pick_candidates()`: reads from hardcoded tables only (`washout_ignition_signal`,
  `call_sweep_log`, `polygon_rvol_scan`, etc.) — does NOT read from `aiem_signal_discoveries`
- No standalone scanner exists for id 4's gap-down reversal conditions

id 4 does not have a dedicated scanner function. It is stored as a record only.

---

## Question 3 — ids 2, 3, 9 exclusion from live systems

**Status: ANSWERED + BUG FIXED**

**What is gated (safe):**

| Code location | Filter | Hypothesis excluded? |
|---|---|---|
| `_mkt_tool_load_discoveries` (~L20968) | `WHERE status = %s` (default `"validated"`) | Yes |
| `_mkt_tool_signal_combination` (~L21544) | `WHERE id IN (...) AND status = 'validated'` | Yes |
| `/stock-api/aiem/discoveries` (~L49040) | `status_filter = request.args.get("status", "validated")` | Yes (by default) |

ids 2 and 3 have no standalone scanner functions. They are records only and are excluded
from all live reads by the above filters.

**What was NOT gated — two bugs found and fixed:**

**Bug 1 — `_scan_washout_ignition_signal()` (main.py ~L49544):**  
Ran at 8:45 AM ET unconditionally. No check against id=9's `status` in
`aiem_signal_discoveries`. If fires found → `_tg_send()` + email sent regardless of status.

**Fix applied (main.py ~L49589–49607):**
```python
if backtest_range is None:
    try:
        with _wi_pg.connect(os.environ["DATABASE_URL"], connect_timeout=3,
                             options="-c statement_timeout=2000") as _sg, _sg.cursor() as _sc:
            _sc.execute("SELECT status FROM aiem_signal_discoveries WHERE id = 9")
            _sg_row = _sc.fetchone()
            if _sg_row is None or _sg_row[0] != "validated":
                _sg_status = _sg_row[0] if _sg_row else "not found"
                print(f"[washout_ignition] SKIPPED: discovery id=9 status='{_sg_status}'"
                      " — live scan and alerts suppressed until re-validated")
                return []
    except Exception as _sg_e:
        print(f"[washout_ignition] status gate check failed: {_sg_e} — skipping for safety")
        return []
```
Backtest mode (`backtest_range is not None`) is exempt — the retest adapter needs to run
regardless of current status to accumulate forward OOS data.

**Bug 2 — `_aiem_paper_pick_candidates()` section 8 (main.py ~L34143):**  
Read from `washout_ignition_signal` table directly with no status check. Comment said
"validated" but did not verify id=9's status at call time.

**Fix applied (main.py ~L34143–34160):**
```python
_cu.execute("SELECT status FROM aiem_signal_discoveries WHERE id = 9")
_wi_status_row = _cu.fetchone()
if _wi_status_row and _wi_status_row[0] == "validated":
    _cu.execute("""SELECT ticker ... FROM washout_ignition_signal ...""")
    for ...:
        _add(...)
else:
    print(f"[aiem_paper] washout_ignition SKIPPED: discovery id=9 status='{...}' (not validated)")
```

**Current id=9 status the gate sees at runtime:**
```
SELECT status FROM aiem_signal_discoveries WHERE id = 9;
→ ('hypothesis',)
```
The 8:45 AM scan will now return `[]` and skip all Telegram/email alerts until id=9 is
promoted back to `validated` by earning real forward OOS evidence.

---

## Question 4 — end-to-end wiring verification

**Status: PARTIAL — signing discrepancy resolved; 7-step trace pending**

**Signing discrepancy resolution:**  
`aiem_provenance.py` exists and implements HMAC-SHA256 `sign_payload()` / `verify_payload()`
for arbitrary JSON payloads. The signing infrastructure that is actually wired to persistent
storage lives in the `quant_agent_sessions` table (confirmed by schema query):

```
quant_agent_sessions columns:
  job_id, question, status, answer, error, created_at, updated_at,
  current_tool, tool_trace, has_image, aiem_signature, signed_at,
  openai_response_id, signed_ts, verify_token, verify_token_expires_at
```

`aiem_signal_discoveries` columns (full list):
```
id, hypothesis_text, conditions_json, horizon, signal_n, signal_win_rate,
signal_avg_ret, baseline_n, baseline_win_rate, baseline_avg_ret, edge_broad,
edge_tight, p_value, oos_edge, status, discovered_at, confirmed_at,
invented_indicator, notes
```

`provenance_hash` and `signed_at` do NOT exist in `aiem_signal_discoveries`.

Prior claims that "cryptographic provenance signing was built" for DISCOVERIES were
incorrect. Signing exists for AIEM chat session outputs (`quant_agent_sessions`), not for
signal discoveries. The `aiem_provenance.py` module exists and is callable but is not wired
to the discoveries table.

**7-step end-to-end wiring trace:** See below — running against id=6 (the only row with
both a valid structured `oos_edge` and Gate 2 pass).

---

## Module 2 Follow-Up Questions — Q1–Q5 (2026-07-02)

### Q1 — id=1 has a `failing` verdict. What kills it?

**Status: ANSWERED — no kill switch exists yet**

Module 2 issued `decay_verdict=failing` for id=1:
```
realized_n        = 905
realized_win_rate = 46.96%   (< 50% random baseline)
realized_p_value  = 0.0      (statistically significant)
delta_vs_discovery = −5.39pp
```

Module 2 does NOT change `aiem_signal_discoveries.status`. That is Module 4 (human
approval gate), which has not been built yet. Until Module 4 exists:

- id=1 `db_status` remains `validated`
- The signal continues to fire in every live context that checks `status = 'validated'`
- No automated suppression occurs based on the `failing` verdict

**Action required (when Module 4 is built):** Module 4 must present the Module 2 verdict,
require explicit human confirmation, and only then flip `status` to `retired`. The kill-switch
design intentionally requires a human in the loop — no automated retirement on a single Module 2
verdict, no matter how statistically decisive.

---

### Q2 — ids 2/3/5/7/8 stuck at `evaluable_pending_columns` — fix

**Status: FIXED — all 5 now show `evaluable_pending_time` (2026-07-02)**

**Root cause:** Module 2's condition-key classifier lumped two distinct situations into one label:
1. "No evaluation path exists" — genuinely unmapped keys, no adapter
2. "Evaluation path exists, waiting for forward time" — alias/chain-adapter keys, adapter ready

Both yielded `evaluable_pending_columns`. That was wrong for ids 2/3/5/7/8, whose conditions
are ALL covered by existing wired adapters.

**Fix applied in `aiem_module2_decay.py`:**
- Added `_CHAIN_ADAPTER_KNOWN_STEMS` frozenset — all chain adapter condition keys (ids 2/3/4/5)
- Replaced `_PENDING_COLUMN_KEYS` with `_TRULY_UNMAPPED_KEYS` (currently empty — no signal has
  a genuinely unmapped key)
- `_classify_condition_keys` now returns a 5-tuple:
  `(is_structural, is_evaluable_direct, truly_unmapped_keys, alias_keys, chain_adapter_keys)`
- Step 3 in `classify_signal`: only `evaluable_pending_columns` when `truly_unmapped_keys`
  is non-empty; alias/chain-adapter-only signals go to `evaluable_pending_time`

**Live endpoint confirmation (`GET /stock-api/aiem/module2-status`):**
```
evaluable_now              = 1   (id=1)
evaluable_pending_time     = 7   (ids 2,3,4,5,6,7,8)
unevaluable_structural     = 1   (id=9)
evaluable_pending_columns  = 0   ← was 5
```

**Why ids 2/3/5/7/8 still show `evaluable_pending_time` and not `evaluable_now`:**
- ids 7,8: discovered today (2026-07-02) — no forward data yet; fwd_days=0
- ids 2,3: horizon=3d, discovered 2026-06-27 — need today's close in polygon_market_daily
  (EOD ingestion lag; fwd_days=3, data arrives tonight or tomorrow)
- id=5: horizon=5d, discovered 2026-06-27 — needs close on/after 2026-07-07; fwd_days=3

These will auto-advance once Module 1's 2:00 AM ET outcome checker runs against
fresh polygon_market_daily data. No code changes needed.

---

### Q3 — Weekly scheduler job for Module 2

**Status: DONE**

APScheduler job added to `main.py` (Sunday 2:30 AM ET, `id="module2_decay_check"`).
Runs after the Sunday Module 1 discovery outcome check (2:00 AM ET).
Calls `_m2.run_module2(conn)` and upserts results to `aiem_module2_evaluations`.

---

### Q4 — id=9 `unevaluable_structural` — is this correct?

**Status: ANSWERED — correct and permanent**

id=9 (Washout Ignition) uses stage-label condition keys (`trough`, `fire_day`, `cross`,
`confirm`) that are steps in a multi-row state machine, not column filters. No `WHERE`
clause on any column can replicate the detection logic, regardless of what columns exist.

`_mkt_washout_ignition_retest()` is the correct evaluation path. It runs independently
of Module 2's condition-key classifier. It will produce retestable=True outcomes once
post-discovery fire events occur (~31/year → est. 1.6 years to n=200).

Module 2's `unevaluable_structural` label for id=9 is accurate and will remain so. No
code change is needed or appropriate.

---

### Q5 — Module 3 vs Module 4 sequencing

**Status: ANSWERED — Module 4 must come first**

Module 4 (human approval gate) is the kill switch for signals that Module 2 marks as
`failing`. Without Module 4, Module 2's verdicts sit in `aiem_module2_evaluations` with
no downstream action — id=1 is the live proof: `decay_verdict=failing` since the first
Module 2 run, still `validated`, still firing.

**Build order: Module 4 → then Module 3.**

Module 3 (forward-looking signal generator / hypothesis promotion) should not be started
until Module 4 exists, because:
1. Module 3 may promote new hypotheses to `validated`
2. If a Module 2 `failing` verdict doesn't retire the old signal, you accumulate validated
   signals with confirmed negative OOS results
3. The audit is not integrity-sound until the feedback loop is closed

---

## Do not close these items by:
- Re-deriving numbers from notes, in-sample data, or anything that isn't a genuinely
  realized, post-discovery outcome
- Filling a gap with a number that only passes because it was constructed to pass
- Summarizing, skipping, or reporting a step as "working" without showing the underlying data
