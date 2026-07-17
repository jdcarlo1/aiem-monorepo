---
name: ASE verifier self-write chain log bug
description: Verifier scripts that directly append to evidence_chain.log corrupt SEQ when run inside verified_run.sh
---

## The Bug

`verified_run.sh` wraps the command as `OUTPUT=$(eval "$CMD" 2>&1)`.
File writes by the subprocess still execute — stdout is captured but
filesystem writes are NOT suppressed.

If a verifier script appends its own stdout to `evidence_chain.log`
(as both `ase_assignment_verification.py` and `ase_risk_classification_verification.py` did),
the log gains ~280 non-JSON lines before `verified_run.sh` appends
its own JSON entry. `SEQ` is computed as `wc -l + 1`, so the next
run sees an inflated line count and gets the wrong sequence number
(e.g., `seq=458` instead of `seq=166`).

## Symptom

`verified_run.sh` reports an unexpectedly large seq number. Running
`verify_chain.sh` shows valid JSON entries but with gaps. The chain
log fails JSON parsing because non-JSON lines are interspersed.

## Fix

**Verifier scripts must never write to `evidence_chain.log` directly.**
They print to stdout only. `verified_run.sh` captures stdout and owns
the chain log exclusively.

To detect if a verifier has the bug:
```
grep -n "evidence_chain\|out_path" <verifier_script>.py
```
If it returns anything, remove the file-write block.

## Recovery When Log Is Already Contaminated

```python
import json
with open('evidence_chain.log', 'r', errors='replace') as f:
    lines = f.readlines()
valid = []
for line in lines:
    s = line.strip()
    if not s:
        continue
    try:
        obj = json.loads(s)
        if 'seq' in obj and 'entry_hash' in obj:
            valid.append(line if line.endswith('\n') else line + '\n')
    except Exception:
        pass
# Keep only the entries you want (e.g., trim back to last clean state)
with open('evidence_chain.log', 'w') as f:
    f.writelines(valid[:N])
```

**Why:** `verified_run.sh` uses line count for SEQ — any non-JSON
lines in the log corrupt all subsequent entries' sequence numbers.

**How to apply:** Any new ASE verifier script: print to stdout only.
Never open the chain log file for writing from within a verifier.
