# Evidence Chain Repair — Permanent Record
# 2026-07-23 | Session 8530e9e7-59ef-4bc2-8765-e5fc093a2462

Directive: Evidence Chain Repair (2026-07-23)
Pre-directive HEAD: 766b459

---

## Item 1 — verified_run.sh Rewrite

### Background
`tools/verified_run.sh` was deleted without authorisation at commit a603aa5 (2026-07-20). The file
is the hash-chained execution wrapper for DPL Phase 3 verification. Its absence broke the evidence
chain for all subsequent DPL runs. This item rewrite the file from scratch, faithfully reproducing
the deleted behaviour.

### Before
```
tools/verified_run.sh: ABSENT (deleted at a603aa5)
retired sha256:        ba6100ae36baab3ab3c2f96817c49207057eea08b6b134f00bf17695ef0a8836
```

### After (rewritten by directive)
```
tools/verified_run.sh: PRESENT (308 lines)
new canonical sha256:  6305cde74d47a5a506f1a8c9fd3dcea780189cf6b344e4a8de6bdf825853f2a3
```

### Test run (live verification)
```
SEQ:       52
GIT_TREE:  DIRTY  (expected — new files uncommitted at time of test)
EXIT:      1      (correct — DIRTY exits 1 per design)
command:   echo TEST_VERIFIED_RUN_OK
output:    TEST_VERIFIED_RUN_OK  (command executed successfully)
chain:     entry appended to artifacts/stock-scanner-api/evidence_chain.log
```

TREE=DIRTY is the correct and expected outcome. The script ran, wrapped the command, logged the
sha256s, executed the command, computed entry_hash, and appended to the chain. Script behaviour
confirmed correct.

### References updated (mutable files only — sealed logs NOT touched)
| File | Old reference | New reference |
|------|--------------|---------------|
| artifacts/stock-scanner-api/tools/t_d21_crontrigger_et.py | `ba6100ae...` (CANONICAL_VR) | `6305cde...` |
| docs/verification/phase3-status.md | `ba6100ae...` | `6305cde...` (with prior noted) |
| docs/verification/phase6-risk-engine-gating-FINAL.md | `ba6100ae...` (×4 summary/disclosure lines) | `6305cde...` |
| docs/verification/audit-gap-remediation-2026-07-23.md | `ba6100ae...` (current hash) | `6305cde...` (REWRITTEN, prior noted) |
| .agents/memory/MEMORY.md | `ba6100ae` in PE chain wrapper entry | `6305cde` |

### Sealed (NOT touched per Data Immutability Rule)
- `artifacts/stock-scanner-api/tools/logs/verified_run_72.log` (chmod 444): historical observation of `ba6100ae` — accurate period record
- `artifacts/stock-scanner-api/tools/logs/verified_run_73.log` (chmod 444): historical observation of `ba6100ae` — accurate period record

Historical raw-output code blocks inside triple-backtick fences (phase6-risk-engine-gating-FINAL.md
lines 291-295, evidence table row 11) record what sha256sum printed at the time of those runs.
They are historical observations and are NOT altered. Annotation lines added adjacent to them.

---

## Item 2 — verify_chain.sh SUMMARY Line Revert

### Background
An undirected commit (20530e2, 2026-07-22) added a SUMMARY print line to
`artifacts/stock-scanner-api/verify_chain.sh`. The line caused the chain script to report
"10/10 PASS" when the correct count was "8/8 graded PASS, 2 SKIP". This created an inflated
pass count in the verification output. The directive required revert to the canonical
(4f280e6, 2026-07-18) version.

### Before (drifted)
```
artifacts/stock-scanner-api/verify_chain.sh
sha256: aa618d45e91e53c059403babf3f5124f73acee3955403434f5480db854949d40
diff:   +print(f"SUMMARY: {len(passes)} PASS  {len(fails)} FAIL")  [ONE line]
```

### After (reverted by directive — Option A)
```
artifacts/stock-scanner-api/verify_chain.sh
sha256: ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f
diff:   -print(f"SUMMARY: {len(passes)} PASS  {len(fails)} FAIL")  [ONE line removed]
```

Matches canonical sha256 from commit 4f280e6 (2026-07-18) exactly.

### References updated
| File | Old reference | New reference |
|------|--------------|---------------|
| docs/verification/phase3-status.md | `aa618d45...` | `ca7896c7...` (with prior noted) |
| docs/verification/phase6-risk-engine-gating-FINAL.md | `aa618d45...` (summary line) | `ca7896c7...` |
| docs/verification/audit-gap-remediation-2026-07-23.md | `aa618d45...` (current, DRIFT status) | `ca7896c7...` (CLOSED) |

---

## Final SHA256 Cross-Check (post all edits)

```
6305cde74d47a5a506f1a8c9fd3dcea780189cf6b344e4a8de6bdf825853f2a3  artifacts/stock-scanner-api/tools/verified_run.sh
ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  artifacts/stock-scanner-api/verify_chain.sh
535fc39a5bd412f3c174cacff31a12a5ec78861b429e0779b04663ca42b1f4d1  artifacts/stock-scanner-api/tools/t_d21_crontrigger_et.py
80900b8f4437e52a61665593bd0916971d31d6153738b575d3c4eeeb218ad359  docs/verification/phase3-status.md
2c781924ec6010edb6528eee50ad58be83f744301338cb6095be16bf04088900  docs/verification/audit-gap-remediation-2026-07-23.md
bbd9b5bb11469fc23b6e72724ed5cce766a86a45ad160aecff21f9d86e8ac86b  .agents/memory/MEMORY.md
```

`docs/verification/phase6-risk-engine-gating-FINAL.md` sha256 is post all Gap B close appends
(see Phase 6 Gap B section below and in that file's Permanent Disclosures).

---

## Git diff summary (this session)
```
7 files changed, 30 insertions(+), 25 deletions(-)
  .agents/memory/MEMORY.md
  artifacts/stock-scanner-api/tools/t_d21_crontrigger_et.py
  artifacts/stock-scanner-api/tools/verified_run_seq          (SEQ counter — test run incremented)
  artifacts/stock-scanner-api/verify_chain.sh
  docs/verification/audit-gap-remediation-2026-07-23.md
  docs/verification/phase3-status.md
  docs/verification/phase6-risk-engine-gating-FINAL.md
```

Commit hash: session commit pending (pre-directive HEAD = 766b459)
