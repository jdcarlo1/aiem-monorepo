# evidence_chain.log — seq=50 Break Investigation
**Date:** 2026-07-26
**Status:** SEALED — forensic investigation complete

---

## Git history check

```
$ git --no-optional-locks log --oneline --follow -- evidence_chain.log | tail -5
(no output)

$ git --no-optional-locks ls-files evidence_chain.log; echo "exit: $?"
(empty)
exit: 0
```

**Finding:** `evidence_chain.log` is **not tracked by git**. Empty output from `git ls-files` with exit 0 = file is not in the git index. `git log` returning nothing confirms no git history exists. Steps 2 and 3 from the directive (git show at first commit, git log -p filtered around seq=50) are **impossible** — no git history exists.

**Forensic dead end for root-cause identification.** Cannot determine when or how seq=50's entry was written or altered. No commit, author, or timestamp is attributable.

---

## Break confirmation — fresh run 2026-07-26

```
$ bash tools/verify_chain.sh
OK  seq=1   entry_hash=f889daee1b008268...
OK  seq=2   entry_hash=6440346d6563963e...
[... seq 3-49 all OK ...]
OK  seq=49  entry_hash=255603b549c79b77...
FAIL at line 50 (seq=50): entry_hash does not match recomputed hash.
  This entry's fields were altered after being logged, OR the log was hand-edited.
  stored entry_hash:     194770030e29e3421bdd6d28e49197ee5ec39ed4ac6bf1b05ea873257a02cda9
  recomputed entry_hash: de8da9fede442970844dd061ce717dde5d29b0f6c11379cd0181f49000fab331

=== CHAIN BROKEN at line 50. The log is not trustworthy past this point. ===
```

Break at seq=50 is **reproducible on 2026-07-26**. Not a stale prior finding.

---

## Correct hash algorithm (verify_chain.sh)

`verify_chain.sh` uses **pipe-delimited** canonical format, NOT JSON serialization:

```python
canonical = f"{prev_hash}|{seq}|{timestamp_utc}|{command}|{exit_code}|{output_sha256}"
entry_hash = sha256(canonical.encode()).hexdigest()
```

---

## Independent recomputation — seq=49 and seq=50

```
seq=49 stored:   255603b549c79b773ee1a78141940bf42d1386ffdbfa6d8cbc2be79eca412463
seq=49 computed: 255603b549c79b773ee1a78141940bf42d1386ffdbfa6d8cbc2be79eca412463
seq=49 match: True

seq=50 stored:   194770030e29e3421bdd6d28e49197ee5ec39ed4ac6bf1b05ea873257a02cda9
seq=50 computed: de8da9fede442970844dd061ce717dde5d29b0f6c11379cd0181f49000fab331
seq=50 match: False
```

seq=50 fields as stored on disk:

```
seq:           50
timestamp_utc: 2026-07-20T21:14:45.604597Z
command:       grep -n 'def run_pipeline_worker\|_t' artifacts/stock-scanner-api/aiem_options_scheduler.py | awk 'NR<=1 || ($1+0)>=2454' | head -30
exit_code:     0
output_sha256: 4112f185ea24b6392f12ba3e4f357603515519f15b40976b748f6b5f18b6fadf
prev_hash:     255603b549c79b773ee1a78141940bf42d1386ffdbfa6d8cbc2be79eca412463
entry_hash:    194770030e29e3421bdd6d28e49197ee5ec39ed4ac6bf1b05ea873257a02cda9
```

seq=50 prev_hash matches seq=49 stored entry_hash: **True** (chain linkage intact).

The stored `entry_hash` for seq=50 does not match the hash computed from the fields currently on disk. One or more fields (command, timestamp_utc, exit_code, or output_sha256) were different when `entry_hash` was written, and were subsequently altered. **Which field changed is not recoverable** — no git history exists.

---

## Correction to prior agent output

An earlier session recomputed seq=49 and seq=50 using a JSON-serialization algorithm and incorrectly reported seq=49 as also failing. That was **wrong** — the correct algorithm is pipe-delimited (see above). seq=49 passes the correct recomputation. Only seq=50 fails. The incorrect claim is retracted here.

---

## Summary

| Item | Finding |
|---|---|
| `evidence_chain.log` tracked by git | No — file is untracked, no git history |
| Break at seq=50 reproducible | Yes — confirmed fresh 2026-07-26 |
| seq=49 entry_hash valid | Yes — passes correct recomputation |
| seq=50 entry_hash valid | No — stored hash does not match recomputed |
| seq=50 prev_hash linkage intact | Yes — correctly references seq=49 stored hash |
| Which field was altered | **Unrecoverable** — no git history |
| Root cause attributable to commit/author | **No** — file is untracked, no forensic trail |
| Data Immutability Rule violation (confirmed tamper) | **Cannot confirm** — alteration is evidenced by hash mismatch, but attribution and mechanism are unrecoverable |

The chain is broken at seq=50. All entries at seq > 50 in `evidence_chain.log` are untrustworthy by the tool's own definition. This is stated as-found. No self-correction performed.
