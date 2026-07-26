# DPL Chain Schema Boundary — archive_sha256 Coverage Cutover
**Date:** 2026-07-26
**Status:** SEALED — do not edit without a separate directive

---

## Schema boundary

| Range | Schema | archive_sha256 in entry_hash? |
|---|---|---|
| SEQ 1 – 132 | C33 (original) | NO — excluded from entry_hash payload |
| SEQ 133 onward | C33+ (corrected) | YES — included in entry_hash payload |

**Cutover SEQ: 133**
**Cutover timestamp:** `2026-07-26T03:42:25.830961Z`

---

## Why this change was made

Finding logged under this session: `archive_sha256` was excluded from `entry_hash` in
`verified_run_chain.jsonl` despite the comment stating the archive is written first
*specifically so `archive_sha256` can be included in the chain entry*. The comment
documented the intent to include it; the C33 exclusion list contradicted that intent.

Consequence of the gap: `archive_sha256` could be edited post-hoc in
`verified_run_chain.jsonl` without breaking `entry_hash`. `post_seal_verify.sh` PSV-4
and PSV-5 provide external binding verification, but only when explicitly invoked. The
chain's own self-verification (`entry_hash` ← `prev_hash` linkage) did not cover
`archive_sha256` at all.

---

## Files changed

| File | sha256 before | sha256 after |
|---|---|---|
| `tools/verified_run.sh` | `5d3880463f47564958609fbf9642528cc7a3c308078ce16ab08f8bd99857b880` | `1dfb771f3516936cef4550eaec485e927fe649dfdbf4c87974e2cd54af669bbf` |
| `artifacts/stock-scanner-api/tools/post_seal_verify.sh` | `c15aee227c470326a74e81fb634cc2ac77538713a013d414e0b9d01dcea97795` | `9f447648c9f4ef6717029656148c7db523a52c142f3541f0236b16abb976b4cc` |

**git diff --stat:**
```
 artifacts/stock-scanner-api/tools/post_seal_verify.sh | 11 +++++++++--
 tools/verified_run.sh                                 | 12 +++++++-----
 2 files changed, 16 insertions(+), 7 deletions(-)
```

---

## What changed in each file

### `tools/verified_run.sh`

Exclusion set change (line ~214):
```diff
-exclude = {'entry_hash', 'type', 'pre_chain_anchor_note', 'archive_sha256'}
+exclude = {'entry_hash', 'type', 'pre_chain_anchor_note'}
```

### `artifacts/stock-scanner-api/tools/post_seal_verify.sh` — PSV-5

Schema-boundary branch added (line ~159):
```diff
-    for k in ('type', 'pre_chain_anchor_note', 'archive_sha256'):
-        e.pop(k, None)
+    _CUTOVER_SEQ = 133
+    if seq_n < _CUTOVER_SEQ:
+        for k in ('type', 'pre_chain_anchor_note', 'archive_sha256'):
+            e.pop(k, None)
+    else:
+        for k in ('type', 'pre_chain_anchor_note'):
+            e.pop(k, None)
```

PSV-5 now uses the old exclusion set for SEQ < 133 (preserving verification of all
historical entries) and the new exclusion set for SEQ ≥ 133.

---

## First entry under C33+ schema

```
seq:           133
ts:            2026-07-26T03:42:25.830961Z
cmd:           echo C33-plus-cutover-SEQ133
entry_hash:    62c245d2944974bb87e587f41d5653144ccf1d90aea9854513485e91861cf590
archive_sha256: ba1794ebb9442bd77113e5e362c16620e646e3c50bcf8ec85e39fe76eb727cd8
```

---

## Evidence: first invocation under new schema

Full `post_seal_verify.sh` output for SEQ 133 (real chain):

```
====== post_seal_verify.sh ======
SEQ=133
TS=2026-07-26T03:42:27Z
=================================

  [POST-SEAL PASS] PSV1_archive_exists
  [POST-SEAL PASS] PSV2_archive_sha_matches_index
    live_sha=ba1794ebb9442bd77113e5e362c16620e646e3c50bcf8ec85e39fe76eb727cd8
    index_sha=ba1794ebb9442bd77113e5e362c16620e646e3c50bcf8ec85e39fe76eb727cd8
  [POST-SEAL PASS] PSV3_chain_entry_exists_for_seq
  [POST-SEAL PASS] PSV4_archive_sha256_3way_binding
    live_archive_sha=ba1794ebb9442bd77113e5e362c16620e646e3c50bcf8ec85e39fe76eb727cd8
    chain_archive_sha=ba1794ebb9442bd77113e5e362c16620e646e3c50bcf8ec85e39fe76eb727cd8
  [POST-SEAL PASS] PSV5_chain_entry_hash_recomputes
  [POST-SEAL PASS] PSV6_prev_hash_continuity
  [POST-SEAL PASS] PSV7_exit_status_matches_archive
  [POST-SEAL FAIL] PSV8_pass_fail_totals_in_archive -- SUMMARY: line not found in archive
  [POST-SEAL PASS] PSV9_cmd_matches_archive

POST-SEAL SUMMARY: 8 PASS  1 FAIL
POST-SEAL FAILED: PSV8_pass_fail_totals_in_archive
=================================
```

PSV8 FAIL is pre-existing and expected for non-verifier commands: PSV8 requires a
`SUMMARY:` line that only verifier runs produce. Unrelated to this directive.

---

## Evidence: mutation check

archive_sha256 for SEQ 133 corrupted to `deadbeefdeadbeef...` in a temp copy of the
chain. PSV run against the corrupted temp chain:

```
  [POST-SEAL FAIL] PSV4_archive_sha256_3way_binding
    TAMPER: live=ba1794ebb9442bd77113e5e362c16620e646e3c50bcf8ec85e39fe76eb727cd8
         != chain=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
  [POST-SEAL FAIL] PSV5_chain_entry_hash_recomputes
    stored=62c245d2944974bb  computed=ca4154b24861de3c

POST-SEAL SUMMARY: 6 PASS  3 FAIL
POST-SEAL FAILED: PSV4_archive_sha256_3way_binding PSV5_chain_entry_hash_recomputes
                  PSV8_pass_fail_totals_in_archive
```

PSV5 stored hash (`62c245d2...`) was sealed with the correct `archive_sha256`. When
`archive_sha256` is altered in the chain entry, PSV5 recomputes a different hash
(`ca4154b2...`) and reports FAIL. This is the proof the fix works: `archive_sha256`
is now chain-internal tamper-evident from SEQ 133 onward.

The temp file was not written back to the real chain. Real chain is unmodified.

---

## Historical entries (SEQ 1–132): no change

Historical entries were sealed under C33 with `archive_sha256` excluded from
`entry_hash`. They are NOT recomputed or rewritten. PSV-5 applies the old exclusion
set for SEQ < 133 and continues to pass for all historical entries.

`evidence_chain.log` (separate system from `verified_run_chain.jsonl`): `verify_chain.sh`
reports a pre-existing break at seq=50. This break predates this directive and is not
caused by the C33+ change. Needs a separate directive to investigate or formally accept.

---

## Immutability statement

No existing entry in `verified_run_chain.jsonl` (SEQ 1–132) was modified, rewritten,
or deleted. The cutover is forward-only. Confirmed by the PSV-5 schema-boundary branch:
old entries continue to verify under the old exclusion set unchanged.

---

## Evidence: SEQ 1-132 unaffected (full re-verify)

Python recomputation of entry_hash for every entry in verified_run_chain.jsonl with
seq <= 132, using old exclusion set `{entry_hash, type, pre_chain_anchor_note, archive_sha256}`.

```
118 PASS  0 FAIL
```

All 118 present entries (seq 0, 15-86, 88-132) verified. Seq 1-14 and seq 87 are absent
from the chain file — gaps that predate this session. No entry_hash changed.

---

## Validator integrity — tools/verify_chain.sh

Tool that produced the evidence_chain.log seq=50 break finding:

```
972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75  tools/verify_chain.sh
```

**Canonical status: agent-recorded only.** Joel has not independently confirmed this
hash. The seq=50 break finding is contingent on this tool being unmodified; its
integrity is not independently verified.

---

## Full 64-char sha256 before/after

| File | Before | After |
|---|---|---|
| `tools/verified_run.sh` | `5d3880463f47564958609fbf9642528cc7a3c308078ce16ab08f8bd99857b880` | `1dfb771f3516936cef4550eaec485e927fe649dfdbf4c87974e2cd54af669bbf` |
| `artifacts/stock-scanner-api/tools/post_seal_verify.sh` | `c15aee227c470326a74e81fb634cc2ac77538713a013d414e0b9d01dcea97795` | `9f447648c9f4ef6717029656148c7db523a52c142f3541f0236b16abb976b4cc` |

---

## Overall status: CLEARED TO PROCEED — not PASS

| Item | Status |
|---|---|
| C33 exclusion list updated (`verified_run.sh`) | CLOSED |
| PSV-5 schema-boundary branch (`post_seal_verify.sh`) | CLOSED |
| First SEQ under new schema recorded (SEQ 133) | CLOSED |
| Mutation check: PSV4+PSV5 FAIL on corrupted entry | CLOSED |
| SEQ 1-132 unaffected — 118 PASS 0 FAIL re-verify | CLOSED |
| Full 64-char before/after sha256 for both files | CLOSED |
| `tools/verify_chain.sh` integrity | OPEN — agent-recorded hash only; Joel confirmation pending |
| `evidence_chain.log` break at seq=50 | OPEN — pre-existing, separate directive needed |
| PSV8 FAIL for non-verifier runs | OPEN — pre-existing, unrelated to this directive |
