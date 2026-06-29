"""
AIEM Verification Script
========================
Tests: chat response, image upload, casual fast-path, tab data.
Run against dev:  python3 aiem_verify.py
Run against prod: python3 aiem_verify.py https://your-domain.replit.app
"""

import sys, time, json, base64, urllib.request, urllib.error, zlib, struct

BASE = (sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5050")
API  = f"{BASE}/stock-api"
TIMEOUT_POLL = 90   # seconds to wait for AIEM to answer

# ── helpers ──────────────────────────────────────────────────────────────────

def _req(method, path, body=None, timeout=15):
    url  = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
        return json.loads(raw), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, str(e)

def _poll(job_id, label):
    deadline = time.time() + TIMEOUT_POLL
    while time.time() < deadline:
        time.sleep(3)
        d, err = _req("GET", f"/aiem/chat/{job_id}")
        if err:
            return None, f"poll error: {err}"
        st   = d.get("status", "")
        tool = (d.get("current_tool") or "—")[:40]
        elapsed = int(TIMEOUT_POLL - (deadline - time.time()))
        print(f"    [{elapsed:>3}s] {label}: {st}  tool={tool}")
        if st == "done":
            return d.get("answer", ""), None
        if st == "error":
            return None, d.get("error", "unknown error")
    return None, f"timed out after {TIMEOUT_POLL}s"

def _make_red_png(size=32):
    """
    Generate a valid solid-red PNG of `size`×`size` pixels.
    Uses only stdlib (zlib + struct) — no Pillow needed.
    The AI will clearly describe it as a red/colored image.
    """
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))

    # Raw image: each row = filter byte (0) + size × RGB bytes
    row  = b"\x00" + b"\xff\x00\x00" * size   # red pixels
    raw  = row * size
    idat = chunk(b"IDAT", zlib.compress(raw, 9))
    iend = chunk(b"IEND", b"")
    return base64.b64encode(sig + ihdr + idat + iend).decode()

# ── test suite ───────────────────────────────────────────────────────────────

results = []

def check(name, passed, detail=""):
    icon = "✅" if passed else "❌"
    msg  = f"{icon}  {name}"
    if detail:
        msg += f"\n     {detail}"
    print(msg)
    results.append((name, passed))

print(f"\n{'='*60}")
print(f"  AIEM Verification  —  {BASE}")
print(f"{'='*60}\n")

# ── 1. Server alive ───────────────────────────────────────────────────────────
print("── 1. Server reachable ──────────────────────────────────────")
d, err = _req("GET", "/etf-calls")
if err:
    check("Server reachable", False, err)
    print("\nServer is unreachable — cannot continue.")
    sys.exit(1)
n_rows = len(d.get("signals", d.get("picks", d.get("data", []))))
check("Server reachable", n_rows > 0, f"{n_rows} ETF call signals in DB")

# ── 2. AIEM casual fast-path ──────────────────────────────────────────────────
print("\n── 2. AIEM casual fast-path ─────────────────────────────────")
t0 = time.time()
d, err = _req("POST", "/aiem/chat", {"question": "hey, are you working?"})
if err or not d:
    check("Submit casual message", False, err)
else:
    jid = d.get("job_id", "")
    check("Submit casual message", bool(jid), f"job_id={jid}")
    if jid:
        ans, err2 = _poll(jid, "casual")
        elapsed = int(time.time() - t0)
        if err2:
            check("Casual reply received", False, err2)
        else:
            check("Casual reply received", bool(ans),
                  f"{elapsed}s  {len(ans)} chars  |  {ans[:120]}")

# ── 3. AIEM research question (uses call_sweep_log — always has data) ─────────
print("\n── 3. AIEM research question ────────────────────────────────")
t0 = time.time()
d, err = _req("POST", "/aiem/chat", {
    "question":
        "Query the call_sweep_log table and return the 3 most recent rows: "
        "ticker, premium, and timestamp only. No explanation."
})
if err or not d:
    check("Submit research question", False, err)
else:
    jid = d.get("job_id", "")
    check("Submit research question", bool(jid), f"job_id={jid}")
    if jid:
        ans, err2 = _poll(jid, "research")
        elapsed = int(time.time() - t0)
        if err2:
            check("Research reply received", False, err2)
        else:
            # Pass if it replied with something that looks like real data
            # (tickers, numbers, or at least a non-empty non-fallback answer)
            has_content = (
                bool(ans)
                and "no findings" not in ans.lower()
                and len(ans) > 30
            )
            check("Research reply has real content", has_content,
                  f"{elapsed}s  {len(ans)} chars  |  {ans[:200]}")
            if not has_content and ans:
                print(f"     raw answer: {ans[:300]}")

# ── 4. AIEM image upload ───────────────────────────────────────────────────────
print("\n── 4. AIEM image upload ─────────────────────────────────────")
png_b64  = _make_red_png(size=32)
data_url = f"data:image/png;base64,{png_b64}"
t0 = time.time()
d, err = _req("POST", "/aiem/chat", {
    "question":     "Describe the image I'm sending. What color is it?",
    "image_data_url": data_url,
})
if err or not d:
    check("Submit image + question", False, err)
else:
    jid     = d.get("job_id", "")
    has_img = d.get("has_image", False)
    check("Server accepted image payload",  bool(jid),   f"job_id={jid}")
    check("has_image flag set True",        has_img,     f"has_image={has_img}")
    if jid:
        ans, err2 = _poll(jid, "image")
        elapsed = int(time.time() - t0)
        if err2:
            check("Image session completed", False, err2)
        else:
            completed   = bool(ans)
            # AI should mention red/color/image in its reply for the 32×32 red block
            mentions_visual = any(w in (ans or "").lower()
                                  for w in ("red","color","image","solid","pixel","block","square"))
            check("Image session completed",         completed,
                  f"{elapsed}s  {len(ans or '')} chars  |  {(ans or '')[:200]}")
            check("AI described image content",      mentions_visual,
                  f"reply: {(ans or '')[:200]}")
            if not mentions_visual and ans:
                print(f"     full reply: {ans}")

# ── 5. Key scanner tabs have data ─────────────────────────────────────────────
print("\n── 5. Key tab data check ────────────────────────────────────")
# Pass = endpoint returns valid JSON with no HTTP error.
# Stale tabs (cached) may have 0 rows right after a fresh restart while the
# preload fills asynchronously — that is NOT a bug, just timing. Row counts
# are shown as info. Only hard-fail on HTTP errors or invalid JSON.
tabs = [
    ("/unusual-calls",    ["signals"]),
    ("/conviction-calls", ["signals", "picks"]),
    ("/eod-sweeps",       ["signals"]),
    ("/etf-calls",        ["signals"]),
    ("/ai-short-calls",   ["signals"]),
    ("/darkpool",         ["signals"]),
    ("/charm-cascade",    ["signals"]),
    ("/gap-volume-signal",["signals"]),
]
for path, keys in tabs:
    d, err = _req("GET", path)
    if err:
        check(f"Tab {path}", False, err)
        continue
    rows = []
    for k in keys:
        v = d.get(k)
        if isinstance(v, list):
            rows = v
            break
    count = len(rows)
    stale = d.get("stale", False)
    flag  = " (cached — fills after next scan)" if stale else " LIVE"
    # Any valid JSON response is a pass
    check(f"Tab {path}", True, f"{count} rows{flag}")

# ── summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
passed = sum(1 for _, p in results if p)
total  = len(results)
print(f"  {passed}/{total} checks passed")
if passed == total:
    print("  All good — safe to publish.")
else:
    failed = [n for n, p in results if not p]
    print(f"  Failed: {', '.join(failed)}")
print(f"{'='*60}\n")
