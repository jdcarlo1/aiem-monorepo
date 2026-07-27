# verified_run.sh Re-Baseline Record — Commit c058d12

**Date confirmed by Joel:** 2026-07-27  
**Status:** PASS

---

## Canonical transition

| State | sha256 | Commit |
|---|---|---|
| Old canonical | `97589232bed62f2dcd6041ed80e92a892217f7f5c29714406b2ffef7106f00b7` | `1f1f296` / `5059f43b33` |
| **New canonical** | `dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826` | `c058d12` |

---

## Raw sha256sum — live file

```
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826  tools/verified_run.sh
```

Matches new canonical exactly (untruncated).

---

## Raw verify_chain.sh output

```
OK  seq=1  entry_hash=ca30a221809e9dd8...  cmd: OPP-040 endpoint + dashboard: route registration + live HTTP tests
OK  seq=2  entry_hash=cb9c06ec267b2569...  cmd: bash /tmp/opp040_verify.sh
OK  seq=3  entry_hash=d40bc4e646626b67...  cmd: bash /tmp/negctrl_item1_v3discovery.sh
OK  seq=4  entry_hash=e4ca1172d4401f93...  cmd: bash /tmp/negctrl_item2_unrecognized.sh
OK  seq=5  entry_hash=96f9b071debe1031...  cmd: bash /tmp/negctrl_item2_unrecognized.sh
OK  seq=6  entry_hash=ac826c7e8ae728a4...  cmd: bash /tmp/negctrl_item2_unrecognized.sh

=== CHAIN VALID: all 6 entries verified, no tampering detected in the log structure. ===
NOTE: this confirms internal consistency of the log only. It does NOT prove the
commands were actually executed as claimed, or that Joel is running this against
his real production database. Spot-check by re-running a sampled command yourself.
```

tools/verify_chain.sh sha256: `4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12` (canonical, unchanged).

---

## Root cause of the canonical change

**Old code (at `97589232`, commit `5059f43`):**

```bash
python3 -c "
import json
entry = {
    ...
    'command': '''$CMD''',
    ...
}
```

**Problem:** Python triple-quoted string `'''$CMD'''` reinterpreted shell escape sequences in `$CMD`. A command containing literal backslash-n (two characters: `\` + `n`) was stored in JSON as a real newline (one character: `0x0a`). The bash `CANONICAL` string above this block computed the sha256 using the raw two-character `\n` from `$CMD`; the verifier read the JSON-decoded one-character newline and recomputed from that — different byte sequences, different sha256. This caused PSV5 (`chain_entry_hash_recomputes`) to fail on any command containing `\n`, `\t`, `\\`, or other escape sequences.

**Fix (at `dce94f6e`, commit `c058d12`):**

```bash
_VR_CMD="$CMD" python3 -c "
import json, os
entry = {
    ...
    'command': os.environ['_VR_CMD'],
    ...
}
```

`os.environ['_VR_CMD']` is a byte-for-byte copy of `$CMD` as the shell set it — no Python escape reinterpretation occurs. The bash sha256 and the stored JSON value are now computed from identical bytes.

---

## 1f1f296 / 5059f43b33 commit duplication note

`1f1f296865684f80591ac4b9493344d88f3ac4bb` and `5059f43b33adcc66d1d19611ede696f41a20ce84` are two distinct Git commit objects with identical timestamp (`2026-07-26 04:45:28 +0000`), identical message ("Update evidence log file name and script references"), and identical `Replit-Commit-Session-Id: fc69526a-f37a-4b1a-8158-dd70b70ad33f`. Both independently resolve to the prior canonical sha256 `97589232` when checked via `git show <hash>:tools/verified_run.sh | sha256sum`. This is a merge/rebase artifact producing two commit objects with identical metadata; both represent the same logical change. No tampering — confirmed via `git show` + `sha256sum` on both objects.

---

## Forensic summary

| Item | Finding |
|---|---|
| git status --porcelain | Clean — working copy matches `c058d12` exactly |
| Commits since `1f1f296` touching file | One: `c058d12` (2026-07-26 23:50:25 UTC) |
| Attribution of `c058d12` | Self-directed bug fix; no prior Joel directive; confirmed legitimate by Joel 2026-07-27 |
| Chain integrity after re-baseline | VALID — all 6 entries OK |
| New canonical | `dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826` |
