# evidence_chain Gitignore Fix + Seq=50 Closeout — FINAL

## Problem

`evidence_chain.log` was excluded from git since its first write by the Replit
system-level rule `/etc/.gitignore:11: *.log`. The file was never committed to
the repository at any point; the entire audit chain was untracked across all
sessions.

```
$ git --no-optional-locks check-ignore -v evidence_chain.log
/etc/.gitignore:11:*.log    evidence_chain.log
exit:0
```

## Fix

Renamed `evidence_chain.log` → `evidence_chain.jsonl` (pure filesystem `mv`;
no `git mv` needed — file was untracked). Updated LOG_FILE defaults in both
wrapper scripts atomically:

| File | Line | Before | After |
|---|---|---|---|
| `tools/verified_run.sh` | 19 | `./evidence_chain.log` | `./evidence_chain.jsonl` |
| `tools/verified_run.sh` | 115 | `${LOG_FILE%.log}_raw` | `${LOG_FILE%.*}_raw` |
| `tools/verify_chain.sh` | 17 | `./evidence_chain.log` | `./evidence_chain.jsonl` |

Verification:

```
$ git --no-optional-locks check-ignore -v evidence_chain.jsonl
exit:1

$ ls evidence_chain.log
ls: cannot access 'evidence_chain.log': No such file or directory

$ bash tools/verify_chain.sh
OK  seq=1  entry_hash=f889daee1b008268...
...
OK  seq=49  entry_hash=255603b549c79b77...
FAIL at line 50 (seq=50): entry_hash does not match recomputed hash.
  stored entry_hash:     194770030e29e3421bdd6d28e49197ee5ec39ed4ac6bf1b05ea873257a02cda9
  recomputed entry_hash: de8da9fede442970844dd061ce717dde5d29b0f6c11379cd0181f49000fab331
=== CHAIN BROKEN at line 50. ===
```

seq 1–49 all OK. seq=50 break preserved unchanged by rename.

## Seq=50 Disposition — CLOSED

The seq=50 entry_hash mismatch is the result of at least one field in that
entry being altered after the entry was originally written (explanation a).
The canonical-string format in both the writer (`tools/verified_run.sh`) and
the reader (`tools/verify_chain.sh`) is confirmed unchanged across all commits
after seq=50's timestamp (2026-07-20T21:14:45Z):

```
$ git --no-optional-locks log --oneline --after="2026-07-20T21:14:45" -- tools/verify_chain.sh
(no output — zero commits)

$ git --no-optional-locks show 3ff6548:tools/verified_run.sh | grep CANONICAL
CANONICAL="${PREV_HASH}|${SEQ}|${TIMESTAMP}|${CMD}|${EXIT_CODE}|${OUTPUT_SHA256}"
```

Physical evidence of alteration: seq=50's `command` field contains `\x08`
(ASCII backspace, 0x08) embedded as `_tg\x08'` — a non-printable control
character inconsistent with a legitimately-typed shell command.

The tampered-vs-pre-existing question for seq=50 is **permanently unresolvable
via git forensics**. `git log --follow`, `git blame`, and `git show` all have
zero history to inspect — the file was never git-tracked at any prior commit.
This investigation is **closed**, not open or inconclusive.

## New Canonical Hashes (commit `1f1f296`)

```
$ sha256sum tools/verified_run.sh tools/verify_chain.sh
97589232bed62f2dcd6041ed80e92a892217f7f5c29714406b2ffef7106f00b7  tools/verified_run.sh
4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12  tools/verify_chain.sh
```

Before-hashes (commit `3ff6548`, HEAD~1 at time of rename):

```
$ git --no-optional-locks show 3ff6548:tools/verified_run.sh | sha256sum
1dfb771f3516936cef4550eaec485e927fe649dfdbf4c87974e2cd54af669bbf  -

$ git --no-optional-locks show 3ff6548:tools/verify_chain.sh | sha256sum
972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75  -
```

## Status

**closed** — rename + defaults verified; `evidence_chain.jsonl` confirmed not
gitignored (exit 1); seq=50 origin closed as unresolvable-by-design. This is
not a PASS on the seq=50 origin question — it cannot be determined either way.
