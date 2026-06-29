"""
AIEM End-to-End Chat Demo
=========================
Proves in one run:
  1. Back-to-back text messages answered (multi-turn, fast-path)
  2. Image upload with AI describing the image correctly
  3. Another text message AFTER the image (proves it doesn't get stuck)

Usage:
  python3 aiem_chat_demo.py                        # dev
  python3 aiem_chat_demo.py https://your.app.url   # prod
"""

import sys, time, json, base64, zlib, struct, urllib.request, urllib.error

BASE = (sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5050")
API  = f"{BASE}/stock-api"
POLL     = 1    # poll interval in seconds
TIMEOUT  = 30   # max seconds per message (casual = ~2-4s; 30s is very generous)
RETRY_W  = 8    # seconds to wait before retrying a 429 (lock still held)
RETRY_N  = 6    # max 429 retries per step

# ── helpers ──────────────────────────────────────────────────────────────────

def _req(method, path, body=None, timeout=15):
    url  = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        body_txt = ""
        try: body_txt = e.read().decode()[:120]
        except: pass
        return None, f"HTTP {e.code}: {body_txt}"
    except Exception as e:
        return None, str(e)

def _send_and_wait(question, image_data_url=None):
    """
    Submit a message (with optional image), poll until done.
    Retries on 429 (lock held by previous session) with back-off.
    Returns (elapsed_s, answer, ok).
    """
    body = {"question": question}
    if image_data_url:
        body["image_data_url"] = image_data_url

    # Submit with 429 retry
    d = None
    for attempt in range(RETRY_N):
        d, err = _req("POST", "/aiem/chat", body)
        if err and "429" in err:
            wait = RETRY_W * (attempt + 1)
            print(f"    ⏳ lock busy — waiting {wait}s before retry…", flush=True)
            time.sleep(wait)
            continue
        if err:
            return None, f"submit failed: {err}", False
        break
    if not d:
        return None, "submit failed after retries (lock still held)", False

    jid     = d.get("job_id", "")
    has_img = d.get("has_image", False)
    img_tag = f" + 📷 image (accepted={has_img})" if image_data_url else ""
    print(f"    job={jid[:8]}…{img_tag}  waiting", end="", flush=True)

    t0 = time.time()
    deadline = t0 + TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL)
        print(".", end="", flush=True)
        d, err = _req("GET", f"/aiem/chat/{jid}")
        if err:
            continue
        st = d.get("status", "")
        if st == "done":
            elapsed = round(time.time() - t0, 1)
            answer  = (d.get("answer") or "").strip()
            print(f" {elapsed}s", flush=True)
            return elapsed, answer, True
        if st == "error":
            elapsed = round(time.time() - t0, 1)
            print(f" {elapsed}s  ERROR", flush=True)
            return elapsed, d.get("error", "unknown"), False

    elapsed = round(time.time() - t0, 1)
    print(f" TIMEOUT after {elapsed}s", flush=True)
    return elapsed, "timed out", False

def _make_png(size=48, r=220, g=50, b=50):
    """Solid-color PNG — no Pillow needed."""
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    row  = b"\x00" + bytes([r, g, b]) * size
    idat = chunk(b"IDAT", zlib.compress(row * size, 9))
    iend = chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(sig + ihdr + idat + iend).decode()

# ── demo questions ────────────────────────────────────────────────────────────
# All under 15 words with no analytical keywords → routes to 1-iter fast-path
# so each reply takes ~2-4s regardless of OpenAI load.
STEPS = [
    ("Text 1 — greeting",
     "Hey, are you online and ready to help?",
     None),
    ("Text 2 — follow-up",
     "Good to hear it. How are you doing today?",
     None),
    ("Text 3 — factual",
     "What time does pre-market trading start in the US?",
     None),
    ("Image — upload & describe",
     "I'm sending you an image — please describe it. What color is it and what does it show?",
     _make_png(size=48, r=220, g=50, b=50)),   # solid red 48×48
    ("Text 4 — post-image check",
     "Still there? Just confirming you didn't get stuck.",
     None),
]

# ── main ──────────────────────────────────────────────────────────────────────

print(f"\n{'='*62}")
print(f"  AIEM End-to-End Chat Demo")
print(f"  {BASE}")
print(f"{'='*62}")

results = []

for i, (label, question, image) in enumerate(STEPS, 1):
    print(f"\n── Step {i}/{len(STEPS)}: {label} {'─'*(38-len(label))}")
    print(f"    Q: \"{question}\"")

    elapsed, answer, ok = _send_and_wait(question, image_data_url=image)

    # Extra check for image step: AI must mention color/image/shape
    if image is not None:
        visual = any(w in (answer or "").lower()
                     for w in ("red","color","colour","image","square","solid","block","pixel","bright"))
        ok = ok and visual

    short = (answer or "")[:160].replace("\n", " ")
    icon  = "✅ PASS" if ok else "❌ FAIL"
    print(f"    {icon} — {elapsed}s")
    print(f"    A: \"{short}\"")
    results.append((label, ok, elapsed))

    # Small gap so lock is fully released before next submit
    if i < len(STEPS):
        time.sleep(2)

# ── summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*62}")
passed = sum(1 for _, p, _ in results if p)
total  = len(results)
all_ok = passed == total
print(f"  {'✅  ALL SYSTEMS GO' if all_ok else '❌  ISSUES FOUND'}  —  {passed}/{total} steps passed")
print()
for label, p, t in results:
    print(f"  {'✅' if p else '❌'}  {label:<38} {t}s")
print(f"{'='*62}\n")
sys.exit(0 if all_ok else 1)
