## SEQ chain discontinuity

SEQ (the monotonic counter in `tools/verified_run_seq`) is a per-workspace counter initialised at 0 when the file was first created; it is not a continuous chain across all time. Runs prior to SEQ=3 (TS_END=2026-07-19T14:51:15Z) used a `/tmp`-backed counter that reset on every VM restart, so those run numbers cannot establish a total ordering of the historical record. Authoritative ordering of all runs uses TS_END (UTC) from the run log.
