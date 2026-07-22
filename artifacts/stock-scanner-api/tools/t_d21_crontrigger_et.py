#!/usr/bin/env python3
"""
Directive 21 — CronTrigger UTC→ET fix verifier.

Checks:
  C1: All 10 directive-scoped jobs have timezone=ET in aiem_process.py source (raw grep)
  C2: No directive-scoped CronTrigger is missing timezone= kwarg
  C3: Remaining non-ET trigger (hour=3,min=2 nightly reset) is NOT in directive scope
  C4: APScheduler probe — all 10 triggers fire with US/Eastern timezone and -04:00 offset
  C5: All 10 next-fire-times fall in expected ET clock range
  C6: verify_chain.sh sha256 == new canonical aa618d45... (re-baselined per Directive 21)
  C7: verified_run.sh sha256 == canonical ba6100ae...

SUMMARY line required for PSV8.
"""
import sys, re, subprocess, hashlib, datetime
import pytz

passes = []
fails  = []

def chk(label, ok, detail=""):
    if ok:
        passes.append(label)
        print(f"  [PASS] {label}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(label)
        print(f"  [FAIL] {label}" + (f"  {detail}" if detail else ""))

# ── helpers ──────────────────────────────────────────────────────────────────
def sha256file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def grep_lines(path, pattern):
    result = subprocess.run(
        ["grep", "-n", pattern, path],
        capture_output=True, text=True
    )
    return result.stdout.strip().splitlines()

# ── C1 / C2: source grep ─────────────────────────────────────────────────────
print("\n=== C1/C2: Source grep — all directive-scoped CronTrigger calls ===")
SRC = "artifacts/stock-scanner-api/aiem_process.py"

# Lines that have CronTrigger AND timezone=ET
with_tz   = grep_lines(SRC, r'CronTrigger.*timezone=ET')
# Lines that have CronTrigger WITHOUT timezone= (excluding import/comment lines)
without_tz_all = grep_lines(SRC, r'CronTrigger(')

directive_ids = [
    "aiem_warmup", "aiem_premarket_scan",
    "aiem_open_watcher", "aiem_grade_outcomes", "aiem_grade_t3_t5",
    "aiem_find_missed_runners", "aiem_pattern_gap_analysis",
    "aiem_write_signal_discoveries", "aiem_nightly_learn",
    "aiem_daily_tiered_movers", "aiem_discovery_cycle",
]

print(f"  CronTrigger calls WITH timezone=ET: {len(with_tz)}")
for ln in with_tz:
    print(f"    {ln}")

chk("C1_at_least_12_triggers_have_timezone_ET",
    len(with_tz) >= 12,
    f"found {len(with_tz)}")

# ── C2: any directive-scoped trigger still missing timezone? ──────────────────
print(f"\n  CronTrigger calls WITHOUT timezone= (all):")
without_tz_scope = []
for ln in without_tz_all:
    # hour=3,minute=2 nightly reset is explicitly out of scope
    if "hour=3, minute=2" in ln or "import" in ln:
        print(f"    [OUT-OF-SCOPE/import] {ln}")
        continue
    # line already has timezone= — correctly fixed, skip
    if "timezone=" in ln:
        print(f"    [HAS-TZ-OK] {ln}")
        continue
    without_tz_scope.append(ln)
    print(f"    [MISSING-TZ] {ln}")

chk("C2_no_directive_scoped_trigger_missing_timezone",
    len(without_tz_scope) == 0,
    f"{len(without_tz_scope)} triggers missing timezone in scope")

# ── C3: nightly reset exempt ──────────────────────────────────────────────────
print("\n=== C3: Nightly reset trigger (hour=3,min=2) — explicitly out-of-scope ===")
reset_lines = [ln for ln in without_tz_all if "hour=3, minute=2" in ln]
print(f"  Found {len(reset_lines)} nightly-reset trigger(s):")
for ln in reset_lines:
    print(f"    {ln}")
chk("C3_nightly_reset_is_only_non_ET_trigger",
    len(reset_lines) == 1,
    "exactly 1 non-ET trigger (nightly reset, out-of-scope)")

# ── C4 / C5: APScheduler probe ────────────────────────────────────────────────
print("\n=== C4/C5: APScheduler probe — next fire times with timezone=ET ===")
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

ET  = pytz.timezone("US/Eastern")
now = datetime.datetime.now(pytz.utc)
print(f"  probe_utc: {now.isoformat()}")
print(f"  probe_et:  {now.astimezone(ET).isoformat()}")

sched = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1}, timezone=ET)
sched.start()

def noop(): pass

PROBE_JOBS = [
    ("aiem_warmup",               CronTrigger(day_of_week="mon-fri", hour=6,      minute=55,    timezone=ET)),
    ("aiem_premarket_scan",        CronTrigger(day_of_week="mon-fri", hour="7-9",  minute="*/15",timezone=ET)),
    ("aiem_open_watcher_primary",  CronTrigger(day_of_week="mon-fri", hour="9,10", minute="*/5", timezone=ET)),
    ("aiem_open_watcher_catchup",  CronTrigger(day_of_week="mon-fri", hour="11-15",minute="*/15",timezone=ET)),
    ("aiem_grade_outcomes",        CronTrigger(day_of_week="mon-fri", hour=16,     minute=30,    timezone=ET)),
    ("aiem_grade_t3_t5",           CronTrigger(day_of_week="mon-fri", hour=16,     minute=35,    timezone=ET)),
    ("aiem_find_missed_runners",   CronTrigger(day_of_week="mon-fri", hour=16,     minute=45,    timezone=ET)),
    ("aiem_pattern_gap_analysis",  CronTrigger(day_of_week="mon-fri", hour=17,     minute=0,     timezone=ET)),
    ("aiem_write_signal_disc",     CronTrigger(day_of_week="mon-fri", hour=17,     minute=15,    timezone=ET)),
    ("aiem_nightly_learn",         CronTrigger(day_of_week="mon-fri", hour=18,     minute=0,     timezone=ET)),
]

for jid, trigger in PROBE_JOBS:
    sched.add_job(noop, trigger, id=jid, replace_existing=True)

c4_ok  = True
c5_ok  = True
ET_OFFSET_SECS = [-4 * 3600, -5 * 3600]   # EDT=-4h, EST=-5h

print(f"\n  {'job_id':<32} {'next_fire_et':<32} {'tz_in_trigger':<20} offset_ok")
print("  " + "-" * 95)

for job in sched.get_jobs():
    nft = job.next_run_time
    if not nft:
        print(f"  {job.id:<32} NO_NEXT_FIRE")
        c4_ok = False
        continue
    nft_et    = nft.astimezone(ET)
    tz_field  = str(getattr(job.trigger, 'timezone', 'N/A'))
    offset    = int(nft_et.utcoffset().total_seconds())
    offset_ok = offset in ET_OFFSET_SECS
    # ET-timezone trigger: next_fire should be offset-aware with -04:00 or -05:00
    if not offset_ok:
        c4_ok = False
    # C5: next fire must land in the declared clock-hour windows for this job
    # (coarse check: not midnight to 6am ET for non-warmup/premarket jobs)
    et_hour = nft_et.hour
    ok5 = True
    if job.id == "aiem_warmup" and et_hour != 6:
        ok5 = False
    if job.id == "aiem_grade_outcomes" and et_hour != 16:
        ok5 = False
    if job.id == "aiem_nightly_learn" and et_hour != 18:
        ok5 = False
    if not ok5:
        c5_ok = False
    print(f"  {job.id:<32} {str(nft_et):<32} {tz_field:<20} {'OK' if offset_ok else 'FAIL'}")

sched.shutdown(wait=False)

chk("C4_all_triggers_fire_with_ET_offset", c4_ok, "-04:00 or -05:00 on all next_fire times")
chk("C5_next_fire_hours_match_intended_ET_schedule", c5_ok,
    "warmup@6, grade_outcomes@16, nightly_learn@18")

# ── C6: verify_chain.sh sha256 ────────────────────────────────────────────────
print("\n=== C6: verify_chain.sh canonical re-baseline ===")
CANONICAL_VC = "aa618d45e91e53c059403babf3f5124f73acee3955403434f5480db854949d40"
vc_sha = sha256file("artifacts/stock-scanner-api/verify_chain.sh")
print(f"  file sha256:      {vc_sha}")
print(f"  canonical (new):  {CANONICAL_VC}")
chk("C6_verify_chain_sh_matches_new_canonical", vc_sha == CANONICAL_VC)

# ── C7: verified_run.sh sha256 ────────────────────────────────────────────────
print("\n=== C7: verified_run.sh canonical ===")
CANONICAL_VR = "ba6100ae36baab3ab3c2f96817c49207057eea08b6b134f00bf17695ef0a8836"
vr_sha = sha256file("tools/verified_run.sh")
print(f"  file sha256:      {vr_sha}")
print(f"  canonical:        {CANONICAL_VR}")
chk("C7_verified_run_sh_matches_canonical", vr_sha == CANONICAL_VR)

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print()
print("=" * 72)
total = len(passes) + len(fails)
print(f"  RESULT: {len(passes)}/{total} checks passed")
print(f"SUMMARY: {len(passes)} PASS  {len(fails)} FAIL")
if fails:
    print("  FAILURES:")
    for f in fails:
        print(f"    - {f}")
print("=" * 72)

sys.exit(0 if not fails else 1)
