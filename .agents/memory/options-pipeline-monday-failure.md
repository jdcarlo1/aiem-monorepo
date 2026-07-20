---
name: Options pipeline post-weekend failure
description: Two bugs cause the options pipeline to fail every Monday (and after 3-day holidays). Both fixed July 2026.
---

## Bug 1 — 48h staleness threshold too strict for weekends

`assert_data_freshness()` was called with a hardcoded `172800s` (48h) limit.
Friday market close → Monday 9:45 AM is always ~65-68h. A 3-day holiday can reach ~89h.
Both exceed 48h → every Monday pipeline run blocked with `REGISTRY_STALE_DATA`.

**Fix:** threshold is now day-of-week aware at the call site in `aiem_options_scheduler.py`:
```python
_dow_now = datetime.now(_ET).weekday()
_freshness_secs = 345600 if _dow_now <= 1 else 172800  # Mon/Tue=96h, else 48h
```
Applied in two places: the blocking `assert_data_freshness` call and the logging quality flag (`_pmd_q`).

## Bug 2 — OSS_GEX_REGIME always snapped as MISSING

`snap_indicator()` in `aiem_options_registries.py` has a hard override:
```python
if raw_value is None and quality_status not in ("ERROR", "STALE"):
    quality_status = "MISSING"
```
This fires regardless of the `quality_status` argument passed in.

`OSS_GEX_REGIME` is a text-only categorical indicator always called with `raw=None, txt=gex_regime`.
Since `raw=None` and quality was "FRESH" (not in the exception set), it was always forced to MISSING.
`assert_no_missing_indicators` then caught it as a required indicator with MISSING status → pipeline blocked.

**Fix:** encode the regime string as a numeric proxy before calling `_rc`:
```python
_gex_raw = (1.0 if gex_regime == "LONG_GAMMA"
            else -1.0 if gex_regime == "SHORT_GAMMA"
            else 0.0) if gex_regime else None
```
Now `raw` is non-None when OSS data exists → `quality_status = "FRESH"`.
If gex_regime is genuinely None (no OSS row for ticker), `raw=None` → MISSING is correct.

**Why:** Any text-only indicator in `_REQUIRED_IDS` will hit this same bug. The pattern is:
- If the indicator's meaningful value is categorical (a string), always encode a numeric proxy as `raw`.
- Never leave `raw=None` for any indicator that appears in `_REQUIRED_IDS`.

## Recovering manually after these failures

```sql
UPDATE options_pipeline_jobs
SET status='PENDING', claimed_at=NULL, executing_at=NULL,
    completed_at=NULL, error_text=NULL, claim_id=NULL
WHERE scan_date = CURRENT_DATE AND status IN ('FAILED','EXECUTING','CLAIMED');
```
Then restart the `options-pipeline-scheduler` workflow.
