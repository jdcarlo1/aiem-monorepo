---
name: AIEM Diagram 3 Governance Layer
description: Pure supervisory governance layer over D1+D2, now growing real Path B active-enforcement checkpoints (G0-G5) on top of the original advisory-only Path A; 20 d3_ tables as of P3; tamper-evident event ledger; NEVER modifies D1/D2 business logic
---

# AIEM Diagram 3 — Autonomous Governance, Self-Optimization & Evolution Layer

**Why:** D3 is the executive governance layer above D1 (orchestration) and D2 (verification). Its purpose is monitoring, integrity enforcement, rollback protection, and optimization recommendations — never code changes.

**Core rule:** D3 reads from production tables only. It never writes to D1/D2 tables. No fabricated metrics.

## Key Files
- `artifacts/stock-scanner-api/aiem_diagram3_governance.py` — 16 phases (phase0-phase15), schema, `install_d3_routes()`, `request_governance_action()`/`check_action_status()`
- `artifacts/stock-scanner-api/aiem_diagram3_verification.py` — independent CLI validator (`verify`/`timeline` commands), re-derives PASS/FAIL from its own SQL rather than trusting the governance module's own claims
- `artifacts/stock-scanner-api/AIEM_DIAGRAM3_STRICT_VERIFICATION_REPORT.md` — full strict verification report (2026-07-10), the authoritative source of truth for what's proven vs. gapped

## 17 D3 Tables (real count — see log-count gotcha below)
d3_architecture_baseline, d3_system_health_snapshots, d3_performance_snapshots,
d3_strategy_registry, d3_model_governance, d3_learning_approvals, d3_change_log,
d3_version_history, d3_rollback_registry, d3_optimization_recommendations,
d3_system_forecasts, d3_security_reports, d3_architecture_status,
d3_executive_reports, d3_evolution_plan, d3_governance_event_links (event ledger),
d3_governance_actions (action/enforcement-status tracking)

**Log-count gotcha:** the boot line `[d3_governance] schema init complete — 72 d3_
tables ready` is mislabeled — `72` = `len(_SCHEMA_STMTS)`, i.e. every CREATE
TABLE/INDEX/TRIGGER statement combined, not a table count. Always get the real
count via `information_schema.tables WHERE table_name LIKE 'd3\_%'` (=17), never
trust that log line as a table count.

## Baseline Hash (first production freeze)
`61a65ca7587d79fd...` (first 16 hex chars used as short ref elsewhere). Frozen
2026-07-08 20:51:19 UTC. 195 modules, 220 tools, 21 D2 stages. Protected=True. Never overwrite.

## 25 Admin Endpoints
All at `/stock-api/admin/d3/` with `X-Admin-Token` auth (verified live via grep of
`install_d3_routes()`, not from memory of an older count):
status, baseline, freeze-baseline, discovery, health, performance, strategy-registry,
model-registry, learning-approvals, change-log, log-change, version-history, rollback,
optimization, forecast, security, architecture, executive-report, generate-report,
evolution-plan, trace/<trace_id>, run-cycle, actions, actions/<action_id>,
actions/<action_id>/recheck

## Wiring in main.py
1. `import aiem_diagram3_governance as _d3_gov` + `_DEFERRED_INITS.append(lambda: _d3_gov.d3_startup())`
2. `_d3_gov.install_d3_routes(app)`
3. **New (2026-07-10):** real per-trade provenance link in
   `_aiem_close_paper_trade_and_run_loop` → `link_paper_trade_close(...)` — the
   ONLY live D2→D3 wiring point across all ~21 D2 stages/18 events (confirmed via
   `aiem_diagram3_verification.py timeline`). A lightweight single-event append,
   not a full 15-phase cycle (too slow for the hot path).

## Critical DB Gotcha
`aiem_paper_trades.status` uses UPPERCASE: `'OPEN'`, `'CLOSED_AIEM'`, `'CLOSED_MANUAL'`, `'CANCELLED'`.
All D3 queries must use these exact strings (not lowercase 'open'/'closed').

## Enforcement scoping — D3 is advisory-only, structurally (confirmed 2026-07-10)
`d3_governance_actions.status` has a DB `CHECK` constraint that physically blocks
the literal value `'ENFORCED'` (proved via a rejected raw `UPDATE`). No code path
anywhere lets D3 block/veto/alter a D2 decision — this is not a bug to fix casually,
it reflects that D2 never calls into D3 and checks a return value before proceeding.
`NOT_ENFORCED` is therefore the correct, permanent status for architecture-drift and
rejected-learning-proposal actions. `ADVISORY_ACKNOWLEDGED` IS a real reachable
status (confirmed via a Phase 6 negative-control test) — it means D3 independently
re-checked the target row's real DB state and found it self-consistent with the
decision, NOT a cross-service ack (no separate D2 "owner" service exists — this is
one Python monolith).

## Tamper-evidence: ledger is trigger-protected, actions table is NOT (gap)
`d3_governance_event_links` has a DB trigger blocking `UPDATE`/`DELETE` on chained
rows (proved live). `d3_governance_actions` (newer, T-F) only has the CHECK-constraint
protection on the `'ENFORCED'` value — rows can otherwise still be updated after
creation. Flagged as a real, undone hardening gap, not fixed this session.

## Test-record disclosure gotcha
`is_test_record` is opt-in per-call, not auto-inferred from context. Production code
paths (phase6, phase9, the trade-close hook) triggered by a deliberately-inserted
upstream test row do NOT automatically propagate `is_test_record=true` into the
resulting `d3_governance_event_links`/`d3_governance_actions` rows unless the caller
explicitly passes it. Always trace via `target_id`/`root_trace_id`, don't trust the
flag alone to find every test-originated row.

## Known Gap: Per-Trace Linkage — PARTIALLY CLOSED (2026-07-10, was "unfixed" before)
Originally: zero D3 columns joined a governance check to an individual `trace_id`/
trade. Now: `d3_governance_event_links` has `root_trace_id`/`diagram3_trace_id`/
`paper_trade_id` columns and IS populated — but only for the one wired event (trade
close). All other D2 stages/events still have zero D3 linkage. Don't claim full
per-trace governance coverage — it's exactly one event type, verified live via
`aiem_diagram3_verification.py timeline --trace-id <id>`.

## Path B active enforcement — now in progress (started 2026-07-10)
The user supplied the full 604-line Path B spec (Sections 1-11, TESTS A-L); the
"unresolved/truncated spec" issue below is now moot for future sessions. Path B adds
6 real gate checkpoints (G0 boot auth, G1 tamper — already closed pre-Path-B, G2
pre-decision block, G3 pre-execution auth, G4 learning/promotion gate, G5
recovery/resume) with a shared per-checkpoint SHADOW/ENFORCE mode config, on top of
the existing Path A advisory ledger. Session-by-session plan lives in
`.local/session_plan.md` (P0-P8) — always check that file for current phase status
before resuming this work; it has the authoritative proof-by-phase detail, this memory
file only holds durable cross-session facts.
**G0 boot authorization shipped and live-verified**: `d3_system_state` (singleton,
6-state CHECK) + `d3_checkpoint_config` (per-checkpoint OFF/SHADOW/ENFORCE, 5s cache) +
append-only `d3_governance_config_history`. `g0_authorize_run(entrypoint, run_kind,
trigger_source, is_test_record)` is the real gate fn — `run_kind="SCAN_ONLY"` is NEVER
blocked by G0 even in ENFORCE+PAUSED (agreed default: outages only block
trade-executing runs). ENFORCE-mode escalation requires `confirm=True`; de-escalation
never does. Wired into both real production call sites:
`_aiem_paper_execute_today` (before RUNNING-row insert, BLOCK writes a `BLOCKED_G0`
execution-log row and releases the lock) and `_run_premarket_open_tracker` (once per
scheduler tick before the per-ticker quote loop). All checkpoints currently seeded
SHADOW (log-only) — no real trade has ever been blocked by G0 yet, by design, pending
a multi-day would-block proof window before flipping to ENFORCE.

**OFF vs SHADOW mode semantics are intentionally different, not the same thing wearing
two names.** SHADOW still evaluates `would_block` and logs it (proof-of-concept data for
later ENFORCE decisions) but never blocks. OFF skips evaluation entirely and always
returns `ALLOW`/`reason_code=CHECKPOINT_OFF`/`enforcement_action=DISABLED` — it's the
"this checkpoint's own judgment doesn't count right now" escape hatch (e.g. checkpoint
logic itself suspected broken), and it also skips the DB-error fail-closed/stale-cache
logic since there's nothing to fail closed on. This pattern (three distinct modes, not
two) should be reused verbatim for every future G-checkpoint (G2-G5), not re-derived.

**P3.5 generalized the G0-only machinery into a reusable D2<->D3 request/decision/ack
framework** (`require_governance_authorization`/`acknowledge_governance_decision`;
`g0_authorize_run()` is now a thin wrapper over it). Durable rules for this framework,
apply to every future checkpoint (G1-G5), not just G0:
- **A checkpoint existing in config ≠ a checkpoint being enforced.** Only wire a real
  policy evaluator when you're actually building that checkpoint's logic; any checkpoint
  without one must raise `NotImplementedError`, never fabricate a decision by falling
  through to a default. "Schema+seed only, mode SHADOW, no evaluator" is a valid,
  intentional interim state — don't treat its mere presence in `d3_checkpoint_config` as
  proof it's live.
- **Never let a DB persist failure flip the decision.** If the request/decision row fails
  to save, the ids must come back `None` and the failure must be tagged in
  `reason_codes` (e.g. `PERSIST_FAILED`) — but the already-computed decision must not be
  silently upgraded or downgraded because persistence failed. When any checkpoint moves
  toward ENFORCE, a `PERSIST_FAILED`+`ALLOW` result must NOT be treated as a valid
  authorization to execute a real trade — this is a known structural sharp edge (decision
  is computed before the request row durably commits), not a bug, but it has to be
  explicitly gated for in the ENFORCE-mode caller logic.
- **Acks must re-read the real decision from the DB, never trust a caller-supplied value**,
  and the "no false ack" guarantee should be enforced at the DB level (composite FK +
  CHECK constraints on the ack table), not just in application code. When negative-testing
  a multi-constraint table like this, test each constraint in isolation — a combined test
  case can pass by hitting a *different* constraint than the one you meant to prove,
  silently leaving the real target constraint unverified.
- **Ack try/except must be scoped tightly around only the ack call itself**, never around
  the surrounding lock-release/return that follows it — an ack failure must never be able
  to alter trade flow. Reuse this exact scoping for every future checkpoint's call site.
- Append-only governance tables (requests/decisions/acks, one row set per checkpoint call)
  have no retention/partitioning story yet — fine at current SHADOW/low-frequency volume,
  revisit before ENFORCE-era per-tick volumes accumulate indefinitely on tables whose
  triggers block DELETE.

**G1 (data-guard-completion) shipped and live-verified (2026-07-10), same pattern as G0:**
`_evaluate_g1_decision` = system-state check (reused G0 logic) AND
`_g1_check_baseline_integrity()` (confirms in-process `_D3_BASELINE_HASH` populated).
Real call site: batch level in `_aiem_paper_execute_today`, right after the 3 real
data-guard gates (kill_switch/daily_loss_limit/portfolio_correlation_risk) pass and before
the macro gate — added `DATA_GUARDS_PASSED`/`DATA_GUARDS_FAILED` bus events around it so the
ledger captures *why* G1 was reached, not just its own decision. Confirmed real bug caught
during this wiring: `set_d3_system_state()` was only invalidating the caller's own
checkpoint's cache — any *other* checkpoint (e.g. G1) would have kept serving a stale
system_state after a flip. Fixed to loop over all `_D3_CHECKPOINTS`. Apply this same
"state changes must invalidate every checkpoint's cache, mode changes only invalidate their
own checkpoint's cache" rule to G2-G5.

**Real table names for the request/decision/ack/ledger correlation quadruple** (differ
from what you'd guess by analogy to `d3_governance_event_links`):
`d3_governance_requests`, `d3_governance_decisions`, `d3_governance_acks` (NOT
`d3_governance_acknowledgements`) — all three keyed by `governance_request_id`/
`governance_decision_id` strings. The ledger table `d3_governance_event_links` has NO
`governance_decision_id` column at all; join it to the triplet via its own `id` column,
which is what `require_governance_authorization` stores as `ledger_event_id` on the
decision row. `d3_governance_decisions.policy_version` is hardcoded `"P3.5"` for every
checkpoint (labels the shared persistence-layer schema generation, not a per-checkpoint
policy version) — don't mistake it for a G-checkpoint identifier.

**G1 post-proof architect review found + fixed 2 real bugs, deferred 1 design decision
(2026-07-10) — apply these lessons to every future checkpoint (G2-G5):**
- **A checkpoint's BLOCK branch position relative to the surrounding `try/finally` matters
  and must be checked per call site, not assumed from copying an earlier checkpoint's
  pattern.** G0's BLOCK branch runs *before* the big `try` in `_aiem_paper_execute_today`
  and correctly releases the run lock explicitly; G1's BLOCK branch runs *inside* that same
  `try` (whose `finally` already unconditionally releases the lock) — copying G0's explicit
  release into G1 caused a double-release `RuntimeError` on any real G1 BLOCK. Never caught
  by the original proof because that proof only called the policy evaluator function
  directly, not the call-site BLOCK branch itself (the only way to reach it in SHADOW mode
  is the fail-closed `except` around `require_governance_authorization`, since SHADOW's
  evaluator never returns `decision=="BLOCK"`). **Lesson: a live proof of a checkpoint's
  evaluator function is NOT a proof of its call-site wiring — verify the surrounding
  try/finally/lock structure independently (AST parse or targeted control-flow
  reproduction) for every new call site, don't just trust the copy-paste pattern.**
- **Per-gate audit payloads must report real per-gate outcomes, never a blanket hardcoded
  "CLEAR."** The 3 real D2 data guards (kill_switch/daily_loss_limit/portfolio_correlation_
  risk) each fail open with only a printed warning on exception — `_dg_outcomes` originally
  hardcoded `"CLEAR"` for 2 of the 3 regardless of what actually happened, a fabricated-PASS
  violation. Fixed: each gate now sets its own outcome var (`CLEAR` /
  `ERRORED_OPEN:<exception>` / `SKIPPED_NO_POS_SIZER`) inside its own try/except.
- **Baseline-row authority is ambiguous and must be resolved before ANY checkpoint reaches
  ENFORCE, not just G1**: `_g1_check_baseline_integrity`/`run_phase0_baseline_freeze` both
  pick the baseline row via `ORDER BY id LIMIT 1` (oldest row), but `force=True` re-baseline
  INSERTs a new row and updates the in-process hash — creating a permanent BASELINE_MISMATCH
  in the same process after any force re-baseline, silently masked again on restart. Not
  fixed yet (deliberately deferred — harmless while every checkpoint is SHADOW-only); must
  decide "latest row" vs. explicit `current`-flag authority before ENFORCE is considered for
  any checkpoint that calls this same integrity check.

**G2 (pre-decision trace-integrity block) shipped and live-verified (2026-07-10), same
request/decision/ack pattern as G0/G1, but per-candidate not per-batch:**
`_g2_mandatory_check_names()` derives the 17 mandatory D2 stage-check names LIVE from
`aiem_registry.DIAGRAM2_STAGE_MAP` (never hardcoded — stays correct if D2 stages are ever
renumbered) and `_g2_check_stage_completeness(trace_id)` queries
`d3_governance_event_links` for which of those 17 actually fired for a given trace.
`require_governance_authorization` now accepts `candidate_trace_id`/`candidate_ticker`
kwargs so a per-candidate check is never accidentally scoped to the ambient contextvar
trace. **There are TWO real trade-creation call sites, not one** — don't assume a single
`INSERT INTO aiem_paper_trades` grep is the whole surface:
1. `main.py` `_aiem_paper_execute_today` (has a real `_d2_trace_id`, so G2 evaluates real
   stage completeness) — BLOCK does a plain `continue` (skips only that candidate, no lock
   held in this per-candidate branch, unlike G0/G1's per-batch BLOCK).
2. `premarket_open_trader.py` `evaluate_ticker()` → `write_paper_pick()` (writes to
   `ai_stock_picks`, fires from the 9:52 AM ET scheduler tick) — this path **never runs
   candidates through the D2 1-17 stage pipeline at all**, so `candidate_trace_id` is
   honestly `None` every time and G2 always reports `NO_TRACE_ID`. This isn't a bug in G2;
   it's real signal that this write path structurally bypasses D2 stage tracking — don't
   "fix" it by fabricating a trace_id just to make G2 report `STAGES_COMPLETE`.
Ledger append-only enforcement has **no test-record carve-out**: attempted cleanup DELETE
of `G2TEST_*` synthetic rows was rejected by the same `d3gel_block_mutation()` trigger that
protects real rows — test rows are permanent in this ledger by design, tag with
`is_test_record=True` and leave them, don't expect to tidy them up later.

**G3 (pre-execution governance authorization) shipped and live-verified (2026-07-10), same
request/decision/ack pattern as G0-G2, evaluated once per trade candidate immediately
before ITS write (not per-batch):**
`_evaluate_g3_decision(run_kind, diagram2_risk_result, execution_mode, model_version,
strategy_version)` has TWO unconditional short-circuits checked before mode/state logic
runs at all: (1) `diagram2_risk_result != "PASS"` → hard BLOCK regardless of G3's own
SHADOW/ENFORCE mode (proves a D2 rejection can never be laundered into a D3 approval —
TEST E); (2) `execution_mode == "LIVE"` → hard BLOCK always (no live broker adapter exists
anywhere in this codebase, so a LIVE request can never be honestly authorized, not even in
SHADOW). For every other candidate it adds 3 new checks unique to G3, alongside the reused
G0-style system-state check: `_g3_check_strategy_approval` (real query against
`d3_strategy_registry.signal_source`), `_g3_check_model_approval` (real query against
`d3_model_governance.model_version`+`deployment_status='active'`), and
`_g3_check_unresolved_actions` (real query against `d3_governance_actions` for
QUARANTINE/CRITICAL/PAUSE-type rows still `status='REQUESTED'`).
**Real, currently-live gaps this checkpoint honestly surfaces in SHADOW mode (not fixed,
not hidden):** 5 of the 11 real pick sources in `_aiem_paper_pick_candidates` (`sweep`,
`oi_buildup`, `washout_ignition`, `layer9_stat`, `squeeze_reversion`) plus
`premarket_open_trader` itself are unregistered in `d3_strategy_registry` → G3 will report
`UNAPPROVED_STRATEGY:<source>` for every one of their candidates. `model_version` is `None`
everywhere (no pick source attaches a per-candidate model version — the unrelated
`model_versions` table from `online_learning.py` tracks a conviction-scoring model, not a
per-trade one) → `MODEL_VERSION_NOT_TRACKED` fires universally. Both are pre-existing
product gaps this checkpoint was designed to surface, not artifacts introduced by this
session.
Two real call sites, both wired with the same BLOCK(`continue`, no lock held)/ALLOW+ack
pattern as G2: `main.py` `_aiem_paper_execute_today` (per-candidate, right before the
`aiem_paper_trades` INSERT; `diagram2_risk_result` is derived from the real `_sizing_gate`
value rather than assumed, so the D2-rejection short-circuit is a provable structural
guarantee) and `premarket_open_trader.py` `evaluate_ticker()` (right before
`write_paper_pick`; `diagram2_risk_result="PASS"` is honest here because this path's own
hard/soft blocker gates already ran, not an assumption).
**Test-harness lesson (apply to future checkpoints):** `acknowledge_governance_decision`'s
`blocked`/`continued` params are write-only — the function's return dict never echoes them
back (only `governance_ack_id`/`governance_decision_id`/`governance_request_id`/
`decision_recorded`). A test asserting on `ack_result.get("blocked")` will always fail
regardless of correctness; the real proof is a direct read-back of the persisted
`d3_governance_acks` row (or asserting `decision_recorded` matches the expected decision,
which IS returned and is the FK-integrity proof described in the function's own docstring).
Controlled-test harness for this checkpoint lives at
`artifacts/stock-scanner-api/aiem_diagram3_g3_verify.py` (renamed from a throwaway
`_test_g3_controlled.py` to match this codebase's permanent `*_verify.py` convention) —
safe to re-run anytime as a regression check; toggles G3 to ENFORCE and system state to
PAUSED as controlled tests, restores real state (SHADOW/NORMAL) in a `finally` block.

**G4 (learning/model promotion gate) shipped and live-verified (2026-07-10), a different
shape from G0-G3 — its single real choke-point is a human-triggered admin HTTP endpoint,
not a background per-tick/per-candidate loop:**
Only `POST /stock-api/admin/learning-proposals/<id>/approve` in `main.py` ever sets
`aiem_learning_proposals.promoted=TRUE` and calls `online_learning.rollback_to_version()` to
flip a `model_versions` row live — this is the one G4 gates, immediately before that real
write (same "gate right before the real write" pattern as G3 before the `aiem_paper_trades`
INSERT). `_evaluate_g4_decision` recomputes, independently from raw fields (never trusting a
caller-supplied boolean), the exact same 3-factor policy `run_phase6_learning_approval()`
already computes ADVISORY-ONLY into `d3_learning_approvals` (performance_ok: new_score >=
current_score; calibration_ok: n_samples>=100; risk_ok: score drift <20%) — the two
implementations have no shared constant and must be updated in lockstep by hand if either
threshold changes. Adds two NEW real checks beyond Phase 6: `_g4_check_rollback_artifact`
(real query on `online_learning.py`'s own `model_versions` table; a model's first-ever
promotion honestly reports `ok=True`/`FIRST_VERSION_NO_ROLLBACK_TARGET` rather than being
blocked on a rollback history it structurally cannot have yet) and
`_g4_check_version_manifest` (real DB cross-check that the proposal row's
`version_saved`+`weights_hash` actually matches a `model_versions` row — catches
data-integrity divergence between the two tables). Two disclosure-only reason codes
(`OOS_VALIDATION:...80_20_HOLDOUT_ONLY...`, `LEAKAGE_CHECK:NO_AUTOMATED_LEAKAGE...`) are
appended every time but deliberately never contribute to `would_block` — hardcoding them as
blocking would freeze all future promotions forever, a different (and in this case worse)
form of dishonesty than a fabricated PASS.
**Real, disclosed pre-existing gap this checkpoint does NOT close (by design — spec calls
for one choke-point)**: `_dc_module3_online_learning_update` (main.py ~1725-1790)
auto-promotes model `discovery_cycle_signal_weights` via `propose_update(promote=True)` with
zero human review and zero D3 involvement, and never reaches the admin endpoint G4 sits in
front of at all.
**Test-harness shape lesson (differs from G0-G3's harness pattern):** because G4's only real
call site is a Flask admin endpoint (not a background loop function you can call directly
and trust to represent production behavior), its verify harness
(`artifacts/stock-scanner-api/aiem_diagram3_g4_verify.py`) drives the REAL running endpoint
over live HTTP with `requests`, using real (not mocked) `model_versions`/
`aiem_learning_proposals` rows tagged with a `G4TEST_` model-name prefix — calling
`_evaluate_g4_decision`/`require_governance_authorization` directly would only prove the
policy math, not that main.py's endpoint wiring (SELECT columns, BLOCK/ALLOW branching,
ack calls) is actually correct. TEST F (accepted=TRUE but n_samples=30<100) -> real HTTP 409,
real promotion write never runs (`promoted` stays FALSE, `model_versions.is_live` stays
FALSE). TEST G (all 3 factors pass, first-ever version) -> real HTTP 200, real promotion
write DOES run (`promoted`=TRUE, `is_live`=TRUE) — confirmed via live production log lines
(`stock-api` workflow) showing the actual 409/200/400 status codes against the real process,
not just the harness's own assertions.

**G5 (recovery/resume + full state machine) shipped and live-verified (2026-07-10), a
different shape again — its own ALLOW decision IS the protected action (unlike G0-G4 which
gate a separate external write):**
`_D3_RECOVERY_GATED_STATES = {PAUSED, RECOVERY_REQUIRED, ROLLBACK_IN_PROGRESS}` deliberately
excludes RESTRICTED (a policy restriction, not an incident-recovery state per spec Section 6)
— don't add RESTRICTED here without re-reading that section. `set_d3_system_state()` now
raises `ValueError` on any direct transition OUT of a gated state unless called with the
internal `_g5_authorized=True` flag, which only `g5_authorize_resume()` ever passes — this
closes what would otherwise be a permanent raw-bypass hole around every other checkpoint.
`g5_authorize_resume(target_state, ...)` calls `require_governance_authorization(checkpoint=
"G5", g5_target_state=...)` then, only on ALLOW, performs the real state change itself in the
same call — the resume entrypoint and the gate are the same function, not two call sites like
G0-G4's "gate, then separately call the real write."
`_g5_check_ledger_chain_integrity()` does a FULL (non-windowed) hash-chain walk via a lazy
import of `aiem_diagram3_verification` (that module imports this one at module level as
`d3gov`, so importing it back at module level here would be circular — import it lazily
inside the function instead). `_g5_check_recovery_verification()` reuses the exact same
`d3_governance_actions status='REQUESTED'` unresolved-action gate G3 already enforces — one
shared real mechanism for "is there an unresolved critical/quarantine/pause action", not two
parallel ones to keep in sync.
G5 is seeded SHADOW at boot like every other checkpoint (NOT auto-ENFORCE) — don't assume a
"recovery gate" defaults to strict; an operator must explicitly flip it to ENFORCE.
**Test-harness lesson specific to G5** (`artifacts/stock-scanner-api/aiem_diagram3_g5_verify.py`):
proving the ENFORCE-mode BLOCK (TEST K2) requires a REAL unresolved action row, and there is
no admin route to create one generically (only `GET .../actions`, `GET .../actions/<id>`,
`POST .../actions/<id>/recheck` exist) — the harness must import
`aiem_diagram3_governance` directly and call `request_governance_action()` in-process to seed
it, then resolve it via `check_action_status()` (which, for any action_type it has no
automated real-state re-check for, honestly defaults to `NOT_ENFORCED` — that's enough to
clear the REQUESTED status and unblock resume). Live-verified: raw
`/g0/system-state` bypass out of a gated state -> HTTP 400 real ValueError; `/g5/resume` happy
path -> HTTP 200 ALLOW with real full-chain verification + real state change; `/g5/resume`
with a real unresolved action -> HTTP 403 BLOCK, state provably unchanged; same request after
resolving the action -> HTTP 200 ALLOW, real state change. All against the live prod DB and
the real running process, not mocked.

**Post-completion architect review of G5 found 1 regression + 1 TOCTOU gap, both closed
same session — apply these two lessons to any future checkpoint that (a) adds a new
raw-bypass guard, or (b) separates a policy-read from its own write:**
- **A new raw-bypass guard on a shared primitive (here, `set_d3_system_state`) will break
  every earlier checkpoint's test harness that called that primitive directly for
  setup/teardown**, not just new code — `aiem_diagram3_g3_verify.py`'s restore-to-NORMAL
  calls broke silently (would have raised `ValueError` on next run) until routed through
  `g5_authorize_resume()` instead. Whenever a checkpoint adds a guard to a function other
  checkpoints' harnesses call raw, grep every `*_verify.py` harness for direct calls to that
  function, not just the new checkpoint's own harness.
- **A policy evaluator that reads state, followed by a separate write that applies the
  decision, has a real TOCTOU window** if anything else can change that state in between
  (concurrent requests, an operator, a scheduler tick). Fix pattern used here: (1) force a
  fresh (non-cached) read at evaluation time to shrink the window, and (2) pass the
  evaluated-against value through to the write as an `_expected_old_state`-style param that
  the write re-checks under a real row lock (`SELECT ... FOR UPDATE`) immediately before
  committing, refusing (not silently proceeding) if it no longer matches. Apply this same
  two-part pattern to any future checkpoint whose ALLOW decision performs its own write.

## Old Path B truncated-spec issue (historical, resolved 2026-07-10)
An earlier, shorter "D2<->D3 integration" doc truncated mid-Section-10 before ever
defining "PATH B", and `user_query` attempts to ask the user hit a tool-level "prompt
already pending" error. Superseded by the full spec above — kept here only as a
reminder that stuck `user_query` calls should be retried/escalated rather than guessed
around.

## How to Apply
- Add governance metadata for any new module: call `log_change(module, reason, expected_impact)`
- Force re-baseline after architectural changes: `POST /d3/freeze-baseline {"force": true}`
- Executive summary on demand: `POST /d3/generate-report`
- Phase 6 learning approval: APPROVE requires new_score >= current_score AND n>=100 samples
- To request a governance decision and track its (advisory) status: `request_governance_action()` then `check_action_status()` — never assume a status of "ENFORCED" is possible, the DB will reject it
