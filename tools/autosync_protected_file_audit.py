#!/usr/bin/env python3
"""
autosync_protected_file_audit.py
---------------------------------
Daily compensating control for the Replit auto-commit TLA gate bypass.

See GOVERNANCE.md for full context.

What this script does:
  1. Reads git log for agent@replit.com commits touching PROTECTED_PATTERNS files.
  2. Cross-references with tools/trading_logic_approvals.jsonl to detect whether a
     TLA approval was consumed within APPROVAL_WINDOW_SECS of the commit timestamp.
  3. Inserts new findings into autosync_protected_file_log (dedup by commit_sha).
  4. Sends a Telegram alert for unapproved commits not yet alerted.

Run standalone:
    python3 tools/autosync_protected_file_audit.py [--since YYYY-MM-DD] [--baseline]

--baseline  : mark inserted rows with baseline=TRUE (used for initial population only)
--since     : override lookback start (default: gate install date 2026-07-30T20:34:43Z)
"""

import argparse
import datetime
import fnmatch
import json
import os
import subprocess
import sys
import urllib.request

import psycopg2

# ── Configuration ─────────────────────────────────────────────────────────────

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Gate went live at this commit timestamp — commits before this are pre-gate.
GATE_LIVE_UTC = "2026-07-30T20:34:43Z"

# From tools/trading_logic_gate.sh — keep in sync with that file.
PROTECTED_PATTERNS = [
    "artifacts/stock-scanner-api/main.py",
    "artifacts/stock-scanner-api/aiem_v3_discovery.py",
    "artifacts/stock-scanner-api/aiem_position_sizing.py",
    "artifacts/stock-scanner-api/aiem_options_*.py",
    "artifacts/stock-scanner-api/aiem_options_pipeline.py",
    "artifacts/stock-scanner-api/aiem_options_scheduler.py",
    "artifacts/stock-scanner-api/aiem_options_dpl.py",
    "artifacts/stock-scanner-api/aiem_strat_engine/scoring.py",
    "artifacts/stock-scanner-api/aiem_strat_scheduler.py",
    "artifacts/stock-scanner-api/aiem_paper_*.py",
]

# Author email used by Replit Agent commits (auto-commit and manual).
AGENT_EMAIL = "agent@replit.com"

# TLA approval must be consumed within this many seconds of commit to count.
APPROVAL_WINDOW_SECS = 120

APPROVALS_FILE = os.path.join(REPO_ROOT, "tools", "trading_logic_approvals.jsonl")


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_protected(path: str) -> bool:
    for pattern in PROTECTED_PATTERNS:
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def load_tla_approvals() -> list[dict]:
    if not os.path.exists(APPROVALS_FILE):
        return []
    with open(APPROVALS_FILE) as f:
        return [json.loads(line) for line in f if line.strip()]


def parse_iso(ts: str) -> datetime.datetime:
    """Parse ISO-8601 with or without Z suffix to UTC-aware datetime."""
    ts = ts.replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(ts).astimezone(datetime.timezone.utc)


def find_tla_for_commit(
    commit_ts: datetime.datetime,
    protected_files: list[str],
    approvals: list[dict],
) -> tuple[bool, str | None]:
    """
    Return (has_approval, approval_id) for a commit.

    Matches a consumed TLA record where:
      - used=True
      - used_at is within APPROVAL_WINDOW_SECS of commit_ts
      - files_covered overlaps with protected_files
    """
    for rec in approvals:
        if not rec.get("used"):
            continue
        used_at_str = rec.get("used_at")
        if not used_at_str:
            continue
        used_at = parse_iso(used_at_str)
        delta = abs((used_at - commit_ts).total_seconds())
        if delta > APPROVAL_WINDOW_SECS:
            continue
        covered = set(rec.get("files_covered", []))
        # Strip leading repo path if needed for comparison
        protected_set = set(protected_files)
        if covered & protected_set:
            return True, rec["approval_id"]
    return False, None


def get_commits_since(since: str) -> list[dict]:
    """
    Return list of dicts for agent@replit.com commits touching protected files
    since `since` (ISO-8601 string).
    """
    # Use git log with NUL separator to handle any commit message content safely.
    result = subprocess.run(
        [
            "git", "-C", REPO_ROOT,
            "log",
            f"--since={since}",
            f"--author={AGENT_EMAIL}",
            "--format=%H%x00%aI%x00%s",
            "--name-only",
        ],
        capture_output=True, text=True, check=True,
    )

    commits = []
    current: dict | None = None

    for line in result.stdout.splitlines():
        if "\x00" in line:
            # Header line: sha\x00ts\x00subject
            parts = line.split("\x00", 2)
            if len(parts) == 3:
                if current and current.get("protected_files"):
                    commits.append(current)
                current = {
                    "sha": parts[0].strip(),
                    "ts": parts[1].strip(),
                    "msg": parts[2].strip(),
                    "all_files": [],
                    "protected_files": [],
                }
        elif line.strip() and current is not None:
            path = line.strip()
            current["all_files"].append(path)
            if is_protected(path):
                current["protected_files"].append(path)

    if current and current.get("protected_files"):
        commits.append(current)

    return commits


def send_telegram_alert(message: str) -> None:
    token = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("  [telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping alert")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            if not resp.get("ok"):
                print(f"  [telegram] send failed: {resp}")
    except Exception as e:
        print(f"  [telegram] error: {e}")


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS autosync_protected_file_log (
                id                  SERIAL PRIMARY KEY,
                commit_sha          TEXT NOT NULL UNIQUE,
                commit_author_email TEXT,
                commit_ts           TIMESTAMPTZ NOT NULL,
                commit_msg          TEXT,
                protected_files     TEXT[] NOT NULL,
                has_tla_approval    BOOLEAN NOT NULL DEFAULT FALSE,
                tla_approval_id     TEXT,
                pre_gate            BOOLEAN NOT NULL DEFAULT FALSE,
                baseline            BOOLEAN NOT NULL DEFAULT FALSE,
                detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                alerted             BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)
    conn.commit()


# ── Main ──────────────────────────────────────────────────────────────────────

def run(since: str, baseline: bool = False) -> dict:
    gate_live = parse_iso(GATE_LIVE_UTC)
    approvals = load_tla_approvals()

    print(f"[autosync-audit] scanning commits since {since} ...")
    commits = get_commits_since(since)
    print(f"[autosync-audit] {len(commits)} commit(s) touching protected files")

    db_url = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(db_url)
    ensure_table(conn)

    inserted = 0
    skipped_dup = 0
    to_alert = []

    for c in commits:
        commit_ts = parse_iso(c["ts"])
        pre_gate = commit_ts < gate_live
        has_tla, tla_id = find_tla_for_commit(commit_ts, c["protected_files"], approvals)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO autosync_protected_file_log
                  (commit_sha, commit_author_email, commit_ts, commit_msg,
                   protected_files, has_tla_approval, tla_approval_id,
                   pre_gate, baseline, alerted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (commit_sha) DO NOTHING
                RETURNING id
                """,
                (
                    c["sha"],
                    AGENT_EMAIL,
                    commit_ts,
                    c["msg"][:255],
                    c["protected_files"],
                    has_tla,
                    tla_id,
                    pre_gate,
                    baseline,
                    False,
                ),
            )
            row = cur.fetchone()
        conn.commit()

        if row is None:
            skipped_dup += 1
            continue

        inserted += 1
        status = "PRE-GATE" if pre_gate else ("TLA-OK" if has_tla else "NO-TLA 🚨")
        print(f"  {c['sha'][:12]}  {c['ts']}  {status}")
        for f in c["protected_files"]:
            print(f"    {f}")

        if not pre_gate and not has_tla:
            to_alert.append(c)

    # Send Telegram alert for unapproved post-gate commits
    if to_alert:
        lines = [f"⚠️ <b>Autosync Gate Audit</b> — {len(to_alert)} unapproved commit(s) touching protected trading-logic files:\n"]
        for c in to_alert:
            lines.append(f"• <code>{c['sha'][:12]}</code> {c['ts']}")
            lines.append(f"  {c['msg'][:80]}")
            for f in c["protected_files"]:
                lines.append(f"  📄 {f}")
            lines.append("")
        lines.append("These commits bypassed the TLA pre-commit gate (Replit auto-commit). Review required.")
        msg = "\n".join(lines)
        print(f"\n[autosync-audit] sending Telegram alert for {len(to_alert)} unapproved commit(s)")
        send_telegram_alert(msg)

        # Mark as alerted
        shas = [c["sha"] for c in to_alert]
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE autosync_protected_file_log SET alerted=TRUE WHERE commit_sha = ANY(%s)",
                (shas,),
            )
        conn.commit()

    conn.close()

    summary = {
        "since": since,
        "commits_found": len(commits),
        "inserted": inserted,
        "skipped_dup": skipped_dup,
        "alerted": len(to_alert),
    }
    print(f"\n[autosync-audit] done: {summary}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=GATE_LIVE_UTC,
                        help="ISO-8601 start timestamp (default: gate install date)")
    parser.add_argument("--baseline", action="store_true",
                        help="Mark inserted rows as baseline=TRUE")
    args = parser.parse_args()
    run(since=args.since, baseline=args.baseline)
