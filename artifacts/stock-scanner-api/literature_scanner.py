"""
literature_scanner.py
------------------------
Periodically scans for new quant research and proposes how it might adapt
to existing signals (options flow, dark pool, gamma/charm, squeeze fuel,
dark pool, float on-demand, sweep detection, sector heat, quant aggregator).

NEVER touches signals, conviction scores, or production tables.
All output is advisory text saved to literature_briefs only.

REQUIRES: DATABASE_URL (or AIEM_DATABASE_URL), ANTHROPIC_API_KEY.
search_fn is optional — default_search_fn() uses arXiv (no API key) with a
DuckDuckGo Instant Answer fallback.
"""

import os
import json
import datetime as dt
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Callable, Optional

import psycopg2
import psycopg2.extras

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


def default_search_fn(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Keyless literature search: arXiv Atom API, then DuckDuckGo Instant Answer.

    Returns list of {title, snippet, url}. Never raises — empty list on failure.
    """
    results: List[Dict[str, str]] = []
    q = (query or "").strip()
    if not q:
        return results

    # ── arXiv ────────────────────────────────────────────────────────────
    try:
        params = urllib.parse.urlencode({
            "search_query": f"all:{q}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        })
        url = f"http://export.arxiv.org/api/query?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "AIEM-literature-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:max_results]:
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            title = re.sub(r"\s+", " ", title)
            summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
            summary = re.sub(r"\s+", " ", summary)[:400]
            link = ""
            for link_el in entry.findall("a:link", ns):
                if link_el.attrib.get("type") == "text/html" or link_el.attrib.get("rel") == "alternate":
                    link = link_el.attrib.get("href", "")
                    break
            if not link:
                link = entry.findtext("a:id", default="", namespaces=ns) or ""
            if title:
                results.append({"title": title, "snippet": summary, "url": link})
    except Exception as e:
        print(f"[literature_scanner] arXiv search error: {e}")

    if results:
        return results[:max_results]

    # ── DuckDuckGo Instant Answer fallback ───────────────────────────────
    try:
        params = urllib.parse.urlencode({
            "q": q,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        })
        url = f"https://api.duckduckgo.com/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "AIEM-literature-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("Heading") or data.get("AbstractText"):
            results.append({
                "title": data.get("Heading") or q,
                "snippet": (data.get("AbstractText") or "")[:400],
                "url": data.get("AbstractURL") or data.get("AbstractSource") or "",
            })
        for topic in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": (topic.get("Text") or "")[:120],
                    "snippet": (topic.get("Text") or "")[:400],
                    "url": topic.get("FirstURL") or "",
                })
            elif isinstance(topic, dict) and topic.get("Topics"):
                for sub in topic["Topics"][:2]:
                    if sub.get("Text"):
                        results.append({
                            "title": (sub.get("Text") or "")[:120],
                            "snippet": (sub.get("Text") or "")[:400],
                            "url": sub.get("FirstURL") or "",
                        })
    except Exception as e:
        print(f"[literature_scanner] DuckDuckGo search error: {e}")

    return results[:max_results]


def run_weekly_scan(queries: Optional[List[str]] = None) -> Dict[str, Any]:
    """Scheduled entry point — scan_and_save with default_search_fn."""
    try:
        init_schema()
    except Exception as e:
        print(f"[literature_scanner] init_schema warning: {e}")
    new_ids = scan_and_save(default_search_fn, queries=queries)
    return {
        "status": "ok",
        "briefs_created": len(new_ids),
        "brief_ids": new_ids,
        "search": "arxiv+duckduckgo",
    }


DDL = """
CREATE TABLE IF NOT EXISTS literature_briefs (
    id SERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sources_json JSONB,
    summary TEXT,
    relevance_to_existing_signals TEXT,
    suggested_next_steps TEXT,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_at TIMESTAMPTZ
);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL found (set AIEM_DATABASE_URL or DATABASE_URL).")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[literature_scanner] schema ready")


DEFAULT_QUERIES = [
    "options flow dark pool signal research 2026",
    "gamma exposure GEX trading signal paper",
    "Hurst exponent regime detection trading",
    "HMM market regime detection equities",
    "VPIN order flow toxicity research",
    "unusual options activity predictive signal academic",
]

SUMMARY_SYSTEM_PROMPT = """You are a quant research analyst producing a brief
for a trader who already runs a multi-layer options-flow conviction-scoring
system (open interest build, gamma, charm, squeeze fuel, dark pool, float
on-demand, sweep detection, sector heat, and a quant aggregator using Hurst
exponent / VPIN / HMM regime detection).

Given search result snippets about a piece of quant research, produce a brief
in JSON with this exact structure and nothing else:
{
  "summary": "2-3 sentence plain-language summary of the finding",
  "relevance_to_existing_signals": "how this connects to or might improve one of the trader's existing signal layers, or 'low relevance' if it doesn't",
  "suggested_next_steps": "a concrete, specific next step IF relevant, or 'no action needed' if not relevant",
  "confidence": "high" | "medium" | "low"
}

Be skeptical by default. Only recommend concrete action for findings that look
methodologically careful (out-of-sample testing mentioned, reasonable sample
size, not just a backtest on cherry-picked tickers)."""


def summarize_with_llm(query: str, search_snippets: List[Dict[str, str]]) -> Dict[str, Any]:
    if not _HAS_ANTHROPIC or not os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "summary":                      "LLM summarization unavailable (no API key/library).",
            "relevance_to_existing_signals": "unknown",
            "suggested_next_steps":         "Set ANTHROPIC_API_KEY to enable automated briefs.",
            "confidence":                   "low",
        }
    try:
        client   = anthropic.Anthropic()
        payload  = {"query": query, "search_results": search_snippets}
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text.split("\n", 1)[-1]
        return json.loads(text)
    except Exception as e:
        return {
            "summary":                      f"LLM error: {e}",
            "relevance_to_existing_signals": "unknown",
            "suggested_next_steps":         "",
            "confidence":                   "low",
        }


def scan_and_save(
    search_fn: Callable[[str], List[Dict[str, str]]],
    queries: Optional[List[str]] = None,
) -> List[int]:
    """Runs each query through search_fn, summarizes, saves to literature_briefs.
    Returns list of new brief IDs. Only writes to literature_briefs — no access
    to any signal, conviction score, or trading-related table."""
    queries = queries or DEFAULT_QUERIES
    new_ids = []
    with _connect() as conn:
        for q in queries:
            snippets = search_fn(q)
            brief    = summarize_with_llm(q, snippets)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO literature_briefs
                        (query, sources_json, summary, relevance_to_existing_signals, suggested_next_steps)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        q, json.dumps(snippets),
                        brief.get("summary", ""),
                        brief.get("relevance_to_existing_signals", ""),
                        brief.get("suggested_next_steps", ""),
                    ),
                )
                new_ids.append(cur.fetchone()[0])
        conn.commit()
    return new_ids


def get_unreviewed_briefs() -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM literature_briefs WHERE reviewed = FALSE ORDER BY scanned_at DESC LIMIT 20"
            )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("scanned_at"):   d["scanned_at"]   = d["scanned_at"].isoformat()
                if d.get("reviewed_at"):  d["reviewed_at"]  = d["reviewed_at"].isoformat()
                rows.append(d)
            return rows


def mark_reviewed(brief_id: int):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE literature_briefs SET reviewed=TRUE, reviewed_at=now() WHERE id=%s",
                (brief_id,),
            )
        conn.commit()


if __name__ == "__main__":
    init_schema()
    print("literature_scanner schema ready.")
