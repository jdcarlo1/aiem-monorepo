#!/usr/bin/env python3
"""Max concurrent open positions across 5 TP-grid ledgers (entry..exit inclusive)."""
from pathlib import Path
from datetime import date, timedelta
from collections import Counter

PROOF = Path(__file__).resolve().parent
FILES = [
    "01_narrow_wing_full_ledger.csv",
    "02_bullish_rr_full_ledger.csv",
    "03_long_put_fly_full_ledger.csv",
    "04_long_call_fly_full_ledger.csv",
    "05_put_ladder_full_ledger.csv",
]

intervals = []
for fname in FILES:
    path = PROOF / fname
    with path.open() as f:
        header = f.readline().strip().split(",")
        assert header[0] == "entry_date" and header[1] == "exit_date", (fname, header[:2])
        for line in f:
            parts = line.strip().split(",")
            if not parts or parts[0] == "entry_date":
                continue
            ed = date.fromisoformat(parts[0])
            xd = date.fromisoformat(parts[1])
            intervals.append((ed, xd))

print(f"n_intervals={len(intervals)}")
assert len(intervals) == 463, len(intervals)

RANGE_START = date(2024, 8, 12)
RANGE_END = date(2026, 8, 7)

delta = Counter()
for ed, xd in intervals:
    delta[ed] += 1
    delta[xd + timedelta(days=1)] -= 1

counts = {}
running = 0
for k in sorted(k for k in delta if k < RANGE_START):
    running += delta[k]

d = RANGE_START
while d <= RANGE_END:
    running += delta.get(d, 0)
    counts[d] = running
    d += timedelta(days=1)

max_c = max(counts.values())
max_dates = sorted([dd for dd, c in counts.items() if c == max_c])
print(f"max_concurrent={max_c}")
print(f"max_concurrent_first_date={max_dates[0].isoformat()}")
print(f"max_concurrent_n_dates={len(max_dates)}")
print(f"max_concurrent_all_dates={','.join(dd.isoformat() for dd in max_dates)}")

top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
print("top10_dates_by_concurrent:")
for dd, c in top:
    print(f"  {c} {dd.isoformat()}")

buckets = {"0-5": 0, "6-10": 0, "11-15": 0, "16-20": 0, "20+": 0}
n_days = len(counts)
for c in counts.values():
    if c <= 5:
        buckets["0-5"] += 1
    elif c <= 10:
        buckets["6-10"] += 1
    elif c <= 15:
        buckets["11-15"] += 1
    elif c <= 20:
        buckets["16-20"] += 1
    else:
        buckets["20+"] += 1

print(f"n_calendar_days_in_range={n_days}")
for k, v in buckets.items():
    pct = 100.0 * v / n_days
    print(f"dist_{k}_days={v} pct={pct:.4f}")

active = [c for c in counts.values() if c >= 1]
print(f"n_days_with_ge1_open={len(active)}")
print(f"avg_concurrent_ge1={sum(active)/len(active):.6f}")
print(f"sum_concurrent_ge1={sum(active)}")
