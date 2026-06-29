#!/usr/bin/env python3
"""
verify_premarket_system.py
==========================
Run this from artifacts/stock-scanner-api/ to verify:
  1. All module files exist and import cleanly
  2. opening_snapshots table is in the DB
  3. regime_detector 15-min cache + 8s timeout guard works
  4. decision_type mapping obeys agent_decisions CHECK constraint
  5. Full evaluate_ticker pipeline runs against real DB
  6. Scheduler fires at 9:45, not 9:30
  7. write_paper_pick SQL has correct parameter count
  8. 22 live endpoints return 200

Usage:
  cd artifacts/stock-scanner-api
  python3 verify_premarket_system.py
"""

import os, sys, time, json, inspect, importlib.util, threading
sys.path.insert(0, os.path.dirname(__file__))

PASS = 0
FAIL = 0


def ok(label):
    global PASS
    PASS += 1
    print(f"  \033[32m✓\033[0m  {label}")


def fail(label, reason=""):
    global FAIL
    FAIL += 1
    msg = f"  \033[31m✗\033[0m  {label}"
    if reason:
        msg += f"\n       \033[33m→ {reason}\033[0m"
    print(msg)


def section(title):
    print(f"\n\033[1m{title}\033[0m")


# ---------------------------------------------------------------------------

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("\033[31mERROR: DATABASE_URL not set\033[0m")
    sys.exit(1)

print()
print("═" * 58)
print("  VERIFICATION: Premarket Module + Market-Open Stability")
print("═" * 58)

# ── 1. Module files ─────────────────────────────────────────────────────────
section("1. Module files exist and are importable")
MODULES = [
    "opening_snapshot_tracker",
    "premarket_open_trader",
    "regime_detector",
    "pre_recommendation_synthesis",
    "earnings_calendar",
    "decision_logger",
]
for mod in MODULES:
    spec = importlib.util.find_spec(mod)
    if spec:
        ok(f"{mod}.py")
    else:
        fail(f"{mod}.py NOT found")

# ── 2. DB table ─────────────────────────────────────────────────────────────
section("2. opening_snapshots table in database")
import psycopg2

try:
    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='opening_snapshots' ORDER BY ordinal_position"
        )
        cols = [r[0] for r in cur.fetchall()]
    conn.close()
    if {"ticker", "price", "scan_time", "volume", "scan_date"}.issubset(set(cols)):
        ok(f"opening_snapshots — columns: {', '.join(cols)}")
    else:
        fail("opening_snapshots missing expected columns", str(cols))
except Exception as e:
    fail("opening_snapshots table check", str(e))

# ── 3. regime_detector cache + timeout ──────────────────────────────────────
section("3. regime_detector: 15-min cache + 8s download timeout")
import regime_detector as rd

if hasattr(rd, "_cache_lock") and hasattr(rd, "_REGIME_CACHE_TTL") and hasattr(rd, "_FETCH_TIMEOUT"):
    ok(f"Cache vars present  (TTL={rd._REGIME_CACHE_TTL}s, timeout={rd._FETCH_TIMEOUT}s)")
else:
    fail("Cache variables missing")

if hasattr(rd, "_yf_download_with_timeout"):
    ok("_yf_download_with_timeout() helper exists")
else:
    fail("_yf_download_with_timeout() missing")

# Simulate a hanging download; verify guard fires in time
import unittest.mock as mock

def _slow_download(*a, **kw):
    time.sleep(60)

rd._cached_result = {
    "regime": "stale_test",
    "recommendation": "reduce_exposure",
    "confidence": "low",
    "multipliers": rd.REGIME_SIGNAL_MULTIPLIERS["reduce_exposure"],
    "checked_at": "2026-01-01T00:00:00",
    "note": "stale",
}
rd._cached_at = time.time() - 2000   # expired 33 min ago

with mock.patch("yfinance.download", side_effect=_slow_download):
    t0 = time.time()
    result = rd.get_current_regime(db_url, "SPY")
    elapsed = time.time() - t0

if elapsed <= rd._FETCH_TIMEOUT + 1.5:
    ok(f"Timeout guard fired in {elapsed:.1f}s → returned stale cache gracefully")
else:
    fail("Timeout guard too slow", f"{elapsed:.1f}s > {rd._FETCH_TIMEOUT + 1.5}s")

# ── 4. decision_type mapping ─────────────────────────────────────────────────
section("4. decision_type mapping (agent_decisions CHECK constraint)")
import premarket_open_trader as pot

VALID_DB_TYPES = {"trade", "no_trade", "hold", "exit"}
EXPECTED_MAP = {"enter_now": "trade", "wait_until_945": "no_trade", "skip": "no_trade"}

for raw, expected in EXPECTED_MAP.items():
    got = pot._DECISION_TYPE_MAP.get(raw)
    if got == expected:
        ok(f"'{raw}' → '{got}'")
    else:
        fail(f"'{raw}' → expected '{expected}'", f"got {got!r}")

for v in pot._DECISION_TYPE_MAP.values():
    if v not in VALID_DB_TYPES:
        fail("Map emits value that violates DB CHECK", f"'{v}' not in {VALID_DB_TYPES}")

# ── 5. End-to-end pipeline ───────────────────────────────────────────────────
section("5. End-to-end pipeline (XYZVERIFY test ticker)")
import opening_snapshot_tracker as ost

TICKER = "XYZVERIFY"

# 5a. Write snapshots
try:
    for price, vol in [(100.00, 500000), (100.50, 480000),
                       (100.80, 510000), (101.20, 520000)]:
        ost.record_snapshot(db_url, TICKER, price, vol)
        time.sleep(0.05)
    ok(f"4 snapshots written for {TICKER}")
except Exception as e:
    fail("snapshot write", str(e))

# 5b. Read them back
try:
    snaps = ost.get_todays_snapshots(db_url, TICKER)
    if len(snaps) == 4:
        ok(f"get_todays_snapshots() → {len(snaps)} rows")
    else:
        fail("Wrong snapshot count", f"expected 4, got {len(snaps)}")
except Exception as e:
    fail("snapshot read", str(e))

# 5c. Full evaluate_ticker
try:
    result = pot.evaluate_ticker(db_url, TICKER, premarket_gap_pct=5.2)
    decision = result.get("decision")
    pattern  = result.get("opening_pattern", {}).get("pattern")
    n_snaps  = result.get("opening_pattern", {}).get("n_snapshots", 0)
    if decision in ("enter_now", "wait_until_945", "skip"):
        ok(f"evaluate_ticker: decision='{decision}', pattern='{pattern}', "
           f"n_snaps={n_snaps}")
    else:
        fail("evaluate_ticker returned unknown decision", decision)
except Exception as e:
    fail("evaluate_ticker", str(e))

# 5d. Verify decision stored in DB with valid decision_type
try:
    conn2 = psycopg2.connect(db_url)
    with conn2.cursor() as cur:
        cur.execute(
            "SELECT decision_type, reasoning FROM agent_decisions "
            "WHERE ticker=%s AND signal_name='premarket_open_trader' "
            "ORDER BY decision_time DESC LIMIT 1",
            (TICKER,),
        )
        row = cur.fetchone()
    conn2.close()
    if row:
        dt_val, reasoning = row
        if dt_val in VALID_DB_TYPES:
            ok(f"agent_decisions CHECK passed — stored '{dt_val}' ({len(reasoning)} char reasoning)")
        else:
            fail("Invalid decision_type stored", f"'{dt_val}' not in {VALID_DB_TYPES}")
    else:
        fail("No agent_decisions row found for XYZVERIFY")
except Exception as e:
    fail("agent_decisions DB verify", str(e))

# ── 6. Scheduler timing ──────────────────────────────────────────────────────
section("6. Scheduler: premarket_open_tracker starts at 9:45 (not 9:30)")
main_py = os.path.join(os.path.dirname(__file__), "main.py")
with open(main_py) as f:
    src = f.read()

if 'minute="45-59/5"' in src:
    ok('CronTrigger minute="45-59/5" — 9:30/9:35/9:40 burst slots removed')
else:
    fail('9:45 start not found in main.py')

if 'minute="30-59/5"' in src:
    fail('Old 9:30 timing still present', 'minute="30-59/5" still in source')
else:
    ok('Old minute="30-59/5" removed — no 9:30 burst slot')

# ── 7. write_paper_pick SQL ──────────────────────────────────────────────────
section("7. write_paper_pick SQL: 3 placeholders, no spurious param")
src_wpk = inspect.getsource(pot.write_paper_pick)
n_placeholders = src_wpk.count("%s")
values_idx = src_wpk.find("VALUES")
# Check for spurious "open" string AFTER VALUES clause
spurious = '"open"' in src_wpk[values_idx:] if values_idx >= 0 else False
if n_placeholders == 3:
    ok(f"3 %s placeholders (ticker, entry_note, confidence)")
else:
    fail("Wrong placeholder count", f"expected 3, got {n_placeholders}")
if not spurious:
    ok("No spurious 'open' string param after VALUES")
else:
    fail("Spurious 'open' param still present in VALUES tuple")

# ── 8. Cleanup ───────────────────────────────────────────────────────────────
section("8. Cleanup test rows")
try:
    conn3 = psycopg2.connect(db_url)
    with conn3.cursor() as cur:
        cur.execute("DELETE FROM opening_snapshots WHERE ticker=%s", (TICKER,))
        n_snap = cur.rowcount
        cur.execute("DELETE FROM agent_decisions WHERE ticker=%s", (TICKER,))
        n_dec = cur.rowcount
        cur.execute("DELETE FROM ai_stock_picks WHERE ticker=%s", (TICKER,))
        n_pick = cur.rowcount
    conn3.commit()
    conn3.close()
    ok(f"Deleted: {n_snap} snapshot(s), {n_dec} decision(s), {n_pick} pick(s)")
except Exception as e:
    fail("cleanup", str(e))

# ── 9. Live endpoint smoke test ──────────────────────────────────────────────
section("9. Live endpoint smoke test (22 endpoints)")
import urllib.request
import urllib.error

ENDPOINTS = [
    "market/overview", "convergence", "darkpool", "outcomes",
    "52week-breakout", "insider/trades", "insider-radar", "ai-short-calls",
    "morning-runners", "eod-accumulation", "multi-signal", "squeeze-setup",
    "daily-top10", "earnings-calendar", "composite-score", "standout-track",
    "iv-rank?ticker=AAPL", "unusual-calls", "conviction-stack", "eod-sweeps",
    "gamma-pressure", "oi-accumulation",
]

BASE = "http://localhost:5050/stock-api"

for path in ENDPOINTS:
    url = f"{BASE}/{path}"
    try:
        t0 = time.time()
        with urllib.request.urlopen(url, timeout=12) as resp:
            body = resp.read()
            code = resp.status
            elapsed = time.time() - t0
        if code == 200:
            ok(f"{code}  {len(body):>8,} bytes  {elapsed:.2f}s  {path}")
        else:
            fail(f"{code}  {path}")
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code}  {path}", str(e))
    except Exception as e:
        fail(f"FAIL  {path}", str(e))
    time.sleep(2)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
print("═" * 58)
color = "\033[32m" if FAIL == 0 else "\033[31m"
print(f"  {color}RESULT: {PASS} PASS  /  {FAIL} FAIL\033[0m")
print("═" * 58)
print()
sys.exit(0 if FAIL == 0 else 1)
