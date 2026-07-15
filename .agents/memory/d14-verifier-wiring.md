---
name: D14 Post-Run Proof Verifier
description: aiem_d14_verifier.py wiring — how D14 evidence checks fire after every 9:42 AM paper-trade run
---

## The rule
Every 9:42 AM paper-trade execution must produce a verified D14 evidence triplet
within 5 minutes of completion, or an alert fires and a retry runs automatically.

## The three required proofs per ticker
- `D14_LAYER9` — layer9_scores DB read completed before debate
- `D14_DEBATE_PRE` — signal_context injected with D14 keys before debate
- `D14_DEBATE_POST` — bull_bear debate ran and recorded verdict

## SHA-256 evidence chain
seed = sha256("d14_chain:<trace_id>:<trade_date>")
sha256[i] = sha256(canonical_hash(ev[i]) + prev_hash[i])
Events WITHOUT sha256/prev_hash fields → legacy pass (backward-compat).

## Wiring points (all three must fire for full coverage)
1. `_log_finish("SUCCESS")` inside `_aiem_paper_execute_today` →
   calls `_aiem_d14_run_verification_async()` (daemon thread)
2. `start_internal_watchdog(d14_verify_fn=...)` in main.py line 7639 →
   calls d14_verify_fn() after execute_fn() in the recovery path
3. `_aiem_d14_retry_debate_only()` — the retry fn; re-reads picks from
   aiem_paper_trades, re-runs layer9+GARCH+debate, writes "d14_retry" events

## Key sentinel: `_d14_chain_hash_after_pre = "genesis"`
Initialized BEFORE the L9+PRE try block so the POST block never sees NameError.
Overwritten inside the PRE capture block after PRE's sha256 is computed.

**Why:** If the PRE capture block raises, the POST block still runs (outer try
continues) and must have a defined prev_hash to link against.

## DB tables
- `paper_trade_d14_verification` — auto-created by _ensure_schema(); one row
  per verification run; contains result, retry_count, chain_valid, missing_proofs
- `paper_trade_job_ledger.d14_verify_result` — TEXT column added by
  ALTER TABLE IF NOT EXISTS; stamped PASS/FAIL/FAIL_AFTER_RETRY

## Flask dead-zone constraint
Both `_aiem_d14_retry_debate_only` and `_aiem_d14_run_verification_async` are
placed AFTER `_aiem_paper_execute_today` and BEFORE the dead-zone boundary
(~line 29315). They are referenced via `globals().get(...)` to avoid
forward-reference errors from the watchdog startup block.
