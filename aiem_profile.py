"""
AIEM Research Path Profiler
===========================
Runs the same research question 3×, polls every 1s, prints per-step timing
from tool_trace (which now carries t_llm_s + t_tool_s per step).

Run:  python3 aiem_profile.py
"""

import sys, time, json, urllib.request, urllib.error

BASE = (sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5050")
API  = f"{BASE}/stock-api"

QUESTION = (
    "Query the call_sweep_log table and return the 3 most recent rows: "
    "ticker, premium, and timestamp only. No explanation."
)
RUNS     = 3
POLL_S   = 1    # poll every 1 second for fine-grained wall-clock timing
TIMEOUT  = 120  # seconds

# ── helpers ──────────────────────────────────────────────────────────────────

def _req(method, path, body=None, timeout=20):
    url  = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, str(e)

def run_once(run_num):
    print(f"\n{'─'*60}")
    print(f"  RUN {run_num}  —  submitting research question")
    print(f"{'─'*60}")

    t_submit = time.time()
    d, err = _req("POST", "/aiem/chat", {"question": QUESTION})
    if err or not d:
        print(f"  ✗ Submit failed: {err}")
        return None

    job_id = d.get("job_id", "")
    print(f"  job_id = {job_id}")

    # ── poll loop — record wall-clock per tool ────────────────────────────
    deadline     = time.time() + TIMEOUT
    last_tool    = None
    tool_wall    = {}   # tool_name → wall-clock seconds observed in that tool
    t_tool_start = None

    while time.time() < deadline:
        time.sleep(POLL_S)
        d, err = _req("GET", f"/aiem/chat/{job_id}")
        if err:
            print(f"  poll error: {err}")
            continue

        st          = d.get("status", "")
        cur_tool    = d.get("current_tool")
        elapsed_tot = round(time.time() - t_submit, 1)

        # Track tool transitions
        if cur_tool != last_tool:
            if last_tool is not None and t_tool_start is not None:
                tool_wall[last_tool] = tool_wall.get(last_tool, 0) + (time.time() - t_tool_start)
            if cur_tool:
                t_tool_start = time.time()
                print(f"  [{elapsed_tot:>5.1f}s]  → {cur_tool}")
            else:
                t_tool_start = None
            last_tool = cur_tool

        if st == "done":
            t_total = round(time.time() - t_submit, 2)
            trace   = d.get("tool_trace") or []

            # Finish tracking last active tool
            if last_tool and t_tool_start:
                tool_wall[last_tool] = tool_wall.get(last_tool, 0) + (time.time() - t_tool_start)

            return {
                "run": run_num,
                "t_total_s": t_total,
                "answer_len": len((d.get("answer") or "")),
                "answer_preview": (d.get("answer") or "")[:120],
                "tool_trace": trace,
                "tool_wall_s": {k: round(v, 2) for k, v in tool_wall.items()},
            }
        if st == "error":
            print(f"  ✗ error: {d.get('error')}")
            return None

    print(f"  ✗ timed out after {TIMEOUT}s")
    return None


def print_breakdown(r):
    if not r:
        return
    print(f"\n  ┌─ Run {r['run']} breakdown ────────────────────────────────")
    print(f"  │  Total wall-clock : {r['t_total_s']}s")
    print(f"  │  Answer length    : {r['answer_len']} chars")
    print(f"  │  Answer preview   : {r['answer_preview']}")
    trace = r["tool_trace"]
    if trace:
        print(f"  │")
        print(f"  │  Tool calls (from server trace):")
        # Reconstruct per-iteration: group by iteration to show LLM + tools
        iters = {}
        for step in trace:
            it = step.get("iteration", 0)
            iters.setdefault(it, []).append(step)

        for it, steps in sorted(iters.items()):
            llm_t = steps[0].get("t_llm_s", "?")
            print(f"  │    iter {it}: LLM call = {llm_t}s")
            for s in steps:
                t_tool = s.get("t_tool_s", "?")
                ok     = "✓" if s.get("ok") else "✗"
                print(f"  │      {ok} {s['tool']:35s}  {t_tool}s")

        # Synthesize "unaccounted" (LLM time after last tool) = total − sum
        sum_llm  = sum(s.get("t_llm_s", 0)  for s in trace if isinstance(s.get("t_llm_s"), (int, float)))
        sum_tool = sum(s.get("t_tool_s", 0) for s in trace if isinstance(s.get("t_tool_s"), (int, float)))
        final_llm = round(r["t_total_s"] - sum_llm - sum_tool, 2)
        print(f"  │")
        print(f"  │  ── Summary ───────────────────────────────────────────")
        print(f"  │  LLM calls total    : {round(sum_llm, 2)}s  ({round(sum_llm/r['t_total_s']*100)}%)")
        print(f"  │  Tool calls total   : {round(sum_tool, 2)}s  ({round(sum_tool/r['t_total_s']*100)}%)")
        print(f"  │  Final LLM/overhead : ~{final_llm}s")
    else:
        print(f"  │  (no tool_trace — fast-path or trace not stored)")
        wall = r.get("tool_wall_s", {})
        if wall:
            print(f"  │  Wall-clock per tool (client-side): {wall}")
    print(f"  └{'─'*52}")


# ── main ─────────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  AIEM Research Path Profiler  —  {BASE}")
print(f"  Question: {QUESTION[:80]}...")
print(f"{'='*60}")

all_results = []
for i in range(1, RUNS + 1):
    result = run_once(i)
    all_results.append(result)
    print_breakdown(result)
    if i < RUNS:
        print("\n  (waiting 3s between runs to avoid lock collision...)")
        time.sleep(3)

# ── cross-run summary ─────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  Cross-run summary")
print(f"{'='*60}")
valid = [r for r in all_results if r]
if valid:
    totals   = [r["t_total_s"] for r in valid]
    llm_tots = [
        round(sum(s.get("t_llm_s", 0) for s in r["tool_trace"]
                  if isinstance(s.get("t_llm_s"), (int, float))), 2)
        for r in valid
    ]
    tool_tots = [
        round(sum(s.get("t_tool_s", 0) for s in r["tool_trace"]
                   if isinstance(s.get("t_tool_s"), (int, float))), 2)
        for r in valid
    ]
    print(f"  Total time    : {totals}  avg={round(sum(totals)/len(totals),1)}s")
    print(f"  LLM time      : {llm_tots}  avg={round(sum(llm_tots)/len(llm_tots),1)}s")
    print(f"  Tool DB time  : {tool_tots}  avg={round(sum(tool_tots)/len(tool_tots),1)}s")
    overhead = [round(totals[i] - llm_tots[i] - tool_tots[i], 2) for i in range(len(valid))]
    print(f"  Overhead/misc : {overhead}  avg={round(sum(overhead)/len(overhead),1)}s")
    print()
    print(f"  VERDICT:")
    avg_total = round(sum(totals)/len(totals), 1)
    avg_llm   = round(sum(llm_tots)/len(llm_tots), 1)
    avg_tool  = round(sum(tool_tots)/len(tool_tots), 1)
    pct_llm   = round(avg_llm / avg_total * 100)
    pct_tool  = round(avg_tool / avg_total * 100)
    print(f"    {avg_total}s avg — LLM calls eat {pct_llm}%, DB tools {pct_tool}%")
    # Is it consistent or variable?
    spread = round(max(totals) - min(totals), 1)
    if spread < 4:
        print(f"    Spread: {spread}s — CONSISTENT (not intermittent)")
    else:
        print(f"    Spread: {spread}s — VARIABLE (intermittent: cold pool, rate-limit, or LLM jitter)")
    # Identify the slowest single step across all runs
    all_steps = [(s.get("tool"), s.get("t_tool_s", 0), s.get("t_llm_s", 0))
                 for r in valid for s in r["tool_trace"]]
    if all_steps:
        slowest_tool = max(all_steps, key=lambda x: x[1] or 0)
        slowest_llm  = max(all_steps, key=lambda x: x[2] or 0)
        print(f"    Slowest tool  : {slowest_tool[0]}  ({slowest_tool[1]}s)")
        print(f"    Slowest LLM   : {slowest_llm[0]}  (preceded by {slowest_llm[2]}s LLM call)")
print(f"{'='*60}\n")
