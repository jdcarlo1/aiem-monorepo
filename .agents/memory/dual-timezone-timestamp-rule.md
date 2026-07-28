---
name: Dual timezone timestamp rule
description: Standing rule — all permanent record files and chain summaries must show both UTC and US Eastern Time for every cited timestamp/date.
---

# Dual Timezone Timestamp Rule

**Established:** 2026-07-27 (directive: Timestamp Logging Standard)

## Rule

Any permanent record file (`docs/verification/*`), chain/directive close-out summary, or date cited in a "closed on X" claim must show both UTC and US Eastern (ET) side by side.

**Format:** `2026-07-28T00:05:57Z UTC / 2026-07-27 20:05 ET`

**Why:** Container clock runs UTC. Timestamps near the ET day boundary (e.g. 00:00–04:00 UTC) read as a future or wrong date to Joel and require a forensic unwind each time (as happened with SEQ=159 citing "2026-07-28" when Joel's local date was 2026-07-27 ET). Showing both prevents ambiguity at the source.

**Applies to:**
- All `docs/verification/*-FINAL.md` and status files
- Chain entry summary tables
- "Date:" / "Date closed:" fields in permanent records
- Any close-out directive response that cites a specific timestamp

**How to apply:**
- UTC offset for US Eastern: EDT = UTC−4 (Mar–Nov), EST = UTC−5 (Nov–Mar). Late July = EDT.
- Simple arithmetic: 2026-07-28T00:05Z → 2026-07-27 20:05 ET (subtract 4h, adjust date if day rolls back).
- For date-only references near midnight UTC: write `2026-07-28 UTC / 2026-07-27 ET` to make the ambiguity explicit.
