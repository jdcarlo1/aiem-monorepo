---
name: verified_run.sh canonical hash
description: Current canonical SHA-256 for tools/verified_run.sh after 2026-07-27 re-baseline
---

## Current canonical (as of 2026-07-27, commit c058d12 re-baseline)

```
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826
```

On-disk verified 2026-07-28:
```
sha256sum /home/runner/workspace/tools/verified_run.sh
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826  tools/verified_run.sh
```

**Why:** The prior canonical `97589232bed62f2dcd6041ed80e92a892217f7f5c29714406b2ffef7106f00b7` was superseded by a re-baseline on 2026-07-27. Any session that cross-checks against the old value will incorrectly flag DRIFT. Use `dce94f6e…` going forward.

**How to apply:** During any standing-checklist pre-flight, compare `sha256sum tools/verified_run.sh` against this value. MISMATCH against the old `97589232…` value is not drift — the tool was legitimately re-baselined.
